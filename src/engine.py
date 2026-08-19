"""
src/engine.py

Orchestrator module for NetSage AI.

Combines:
  1. Deterministic checks (checker.py) — always runs first.
  2. LLM prompt generation + inference (Anthropic API) — runs when the
     deterministic checker finds nothing, OR always, depending on
     system_config.json's "checker.run_before_llm" setting (in this
     implementation the LLM is always consulted so the human reviewer
     gets a full structured diagnosis either way; the checker's hits are
     merged in as high-confidence evidence).

Produces a single structured JSON diagnostic object per case matching the
schema documented in prompts/diagnose_prompt.md:

{
  "root_cause": str,
  "osi_layer": str,
  "confidence": "low" | "medium" | "high",
  "evidence": str,
  "next_command": str,
  "fix_steps": [str, ...],
  "requires_human_review": true,
  "source": "deterministic" | "llm" | "hybrid"
}

If ANTHROPIC_API_KEY is not set, engine.py falls back to a deterministic-
only diagnosis (or a clearly-labeled placeholder) so the rest of the
pipeline (dashboard, logging) can still be exercised offline / in class
without API access.
"""

import json
import os
import re
import csv
from pathlib import Path
from typing import Optional

import checker  # local import, src/checker.py

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "system_config.json"
PROMPT_PATH = BASE_DIR / "prompts" / "diagnose_prompt.md"
CASES_CSV = BASE_DIR / "data" / "cases.csv"

SYSTEM_PROMPT = """You are NetSage AI, a network-troubleshooting assistant for Cisco IOS /
Packet Tracer lab environments. You help a human network engineer diagnose
faults — you never claim to have fixed anything yourself, and you never
imply a command has been executed. A human reviewer always approves,
edits, or rejects your output before any change is deployed.

You will be given a symptom description, a topology note, and captured
show-command output ("evidence").

Return your answer as a single JSON object and nothing else, matching
exactly this schema:

{
  "root_cause": "string, one or two sentences",
  "osi_layer": "Layer 1 | Layer 2 | Layer 3 | Layer 3/4 | Layer 4 | Layer 7",
  "confidence": "low | medium | high",
  "evidence": "string, the specific show-output line(s) that support the conclusion",
  "next_command": "string, a single show/diagnostic command",
  "fix_steps": ["array", "of", "short CLI or procedural steps"],
  "requires_human_review": true
}

Ground your answer only in the evidence given. Do not invent show output.
Output valid JSON only — no markdown fences, no commentary."""


def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def load_case(case_id: str) -> dict:
    with open(CASES_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["case_id"] == case_id:
                return row
    raise ValueError(f"Case {case_id} not found")


def _call_anthropic(symptom: str, topology_note: str, show_outputs: str, config: dict) -> Optional[dict]:
    """Call the Anthropic API for LLM-based diagnosis. Returns parsed dict or None on failure."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    try:
        import anthropic
    except ImportError:
        return None

    client = anthropic.Anthropic(api_key=api_key)
    user_message = (
        f"Symptom: {symptom}\n"
        f"Topology note: {topology_note}\n"
        f"Show-command output (evidence): {show_outputs}\n\n"
        "Diagnose per the system instructions and return JSON only."
    )

    try:
        response = client.messages.create(
            model=config.get("model", "claude-sonnet-4-6"),
            max_tokens=config.get("max_tokens", 1000),
            temperature=config.get("temperature", 0.2),
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
        text = "".join(block.text for block in response.content if block.type == "text")
        cleaned = re.sub(r"^```json\s*|\s*```$", "", text.strip())
        return json.loads(cleaned)
    except Exception as e:
        print(f"[engine] LLM call failed: {e}")
        return None


def _fallback_diagnosis(case_row: dict, checker_result: checker.CheckerResult) -> dict:
    """Offline fallback: build a diagnosis purely from deterministic checker hits
    when no API key is configured. Clearly labeled as lower-confidence."""
    if checker_result.hits:
        top_hit = checker_result.hits[0]
        return {
            "root_cause": top_hit.message,
            "osi_layer": case_row.get("osi_layer", "Unknown"),
            "confidence": "medium" if top_hit.severity == "High" else "low",
            "evidence": top_hit.matched_text,
            "next_command": case_row.get("next_command", "show running-config"),
            "fix_steps": [
                "Review the deterministic rule hit above",
                "Confirm root cause manually before applying any change",
                "No LLM inference available (ANTHROPIC_API_KEY not set) — deterministic-only result",
            ],
            "requires_human_review": True,
        }
    return {
        "root_cause": "Deterministic checker found no known pattern; LLM inference unavailable "
                       "(set ANTHROPIC_API_KEY to enable full diagnosis).",
        "osi_layer": case_row.get("osi_layer", "Unknown"),
        "confidence": "low",
        "evidence": case_row.get("show_outputs", ""),
        "next_command": case_row.get("next_command", "show running-config"),
        "fix_steps": ["Manual investigation required — no automated diagnosis available"],
        "requires_human_review": True,
    }


def diagnose(case_id: str) -> dict:
    """Full pipeline for a single case: deterministic checker -> LLM inference -> merged result."""
    config = load_config()
    case_row = load_case(case_id)

    checker_result = checker.run_checker(
        case_id, case_row["show_outputs"], case_row.get("topology_note", "")
    )

    llm_result = _call_anthropic(
        case_row["symptom"], case_row.get("topology_note", ""), case_row["show_outputs"], config
    )

    if llm_result:
        result = llm_result
        result["source"] = "hybrid" if checker_result.hits else "llm"
    else:
        result = _fallback_diagnosis(case_row, checker_result)
        result["source"] = "deterministic"

    result["requires_human_review"] = True
    result["case_id"] = case_id
    result["checker_status"] = checker_result.status
    result["checker_hits"] = [h.__dict__ for h in checker_result.hits]
    return result


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="NetSage AI diagnosis orchestrator")
    parser.add_argument("--case", required=True, help="Case ID to diagnose, e.g. NET-001")
    args = parser.parse_args()

    diagnosis = diagnose(args.case)
    print(json.dumps(diagnosis, indent=2))
