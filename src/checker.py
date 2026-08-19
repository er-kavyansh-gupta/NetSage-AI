"""
src/checker.py

Deterministic rule-based validation engine for NetSage AI.

This module scans captured `show_outputs` text (and optionally a
topology_note) for well-known Cisco IOS / Packet Tracer misconfiguration
patterns using regular expressions. It never calls an LLM — every result
here is a deterministic, explainable match, which is why it runs BEFORE
the LLM diagnosis step in engine.py.

Usage (standalone):
    python src/checker.py --case NET-001
    python src/checker.py --all
"""

import argparse
import csv
import re
import sys
from pathlib import Path
from dataclasses import dataclass, field
from typing import List

BASE_DIR = Path(__file__).resolve().parent.parent
CASES_CSV = BASE_DIR / "data" / "cases.csv"


@dataclass
class RuleHit:
    rule_id: str
    message: str
    severity: str
    matched_text: str


@dataclass
class CheckerResult:
    case_id: str
    status: str  # "ERRORS_DETECTED" or "NO_ERRORS_DETECTED"
    hits: List[RuleHit] = field(default_factory=list)

    def to_dict(self):
        return {
            "case_id": self.case_id,
            "status": self.status,
            "hits": [h.__dict__ for h in self.hits],
        }


# ---------------------------------------------------------------------------
# Rule definitions
# Each rule: (rule_id, compiled regex, message template, severity)
# The regex is matched against the combined show_outputs + topology_note text.
# ---------------------------------------------------------------------------

RULES = [
    (
        "ADMIN_DOWN",
        re.compile(r"(\S+)\s+is administratively down", re.IGNORECASE),
        "Interface {0} is administratively down (shut).",
        "High",
    ),
    (
        "LINE_PROTOCOL_DOWN",
        re.compile(r"(\S+)\s+is (?:up|down), line protocol is down", re.IGNORECASE),
        "Interface {0} has line protocol down — check Layer 1/2 (cable, encapsulation, keepalives).",
        "High",
    ),
    (
        "MISSING_ENCAPSULATION",
        re.compile(r"(no 'encapsulation dot1Q[^']*'\s*line present)", re.IGNORECASE),
        "Sub-interface is missing an 802.1Q encapsulation statement: {0}.",
        "High",
    ),
    (
        "VLAN_NOT_EXIST",
        re.compile(r"vlan (\d+)\s*\(?[^)]*does not exist", re.IGNORECASE),
        "Port is assigned to VLAN {0}, which does not exist on the switch.",
        "High",
    ),
    (
        "DUPLICATE_IP",
        re.compile(r"duplicate address\s+(\d{1,3}(?:\.\d{1,3}){3})", re.IGNORECASE),
        "Duplicate IP address detected: {0}.",
        "High",
    ),
    (
        "DUPADDR_LOG",
        re.compile(r"%IP-4-DUPADDR", re.IGNORECASE),
        "Switch/router log shows a %IP-4-DUPADDR duplicate-address event.",
        "High",
    ),
    (
        "DHCP_POOL_EXHAUSTED",
        re.compile(r"0 addresses (?:leased|in pool)", re.IGNORECASE),
        "DHCP pool appears exhausted or unconfigured (0 addresses available).",
        "Medium",
    ),
    (
        "GATEWAY_MISMATCH",
        re.compile(
            r"default[- ]router\s+(\d{1,3}(?:\.\d{1,3}){3}).{0,60}actual gateway is\s+(\d{1,3}(?:\.\d{1,3}){3})",
            re.IGNORECASE | re.DOTALL,
        ),
        "Configured default-router {0} does not match the actual gateway {1}.",
        "Medium",
    ),
    (
        "WRONG_MASK",
        re.compile(r"mask\s+255\.255\.255\.0\s*\(incorrect\)", re.IGNORECASE),
        "Host is using an incorrect subnet mask (255.255.255.0 flagged as incorrect for this segment).",
        "Medium",
    ),
    (
        "ACL_NO_PERMIT",
        re.compile(r"deny ip any any.{0,80}no permit for", re.IGNORECASE | re.DOTALL),
        "ACL has no explicit permit rule for required traffic; falls through to deny: {0}.",
        "High",
    ),
    (
        "ACL_ORDER_ISSUE",
        re.compile(r"line 5.{0,120}line 10, unreachable", re.IGNORECASE | re.DOTALL),
        "ACL entry ordering issue detected — a later permit rule is unreachable because a broader deny rule precedes it.",
        "Medium",
    ),
    (
        "NAT_MISSING_OUTSIDE",
        re.compile(r"missing 'ip nat outside'", re.IGNORECASE),
        "NAT outside interface is missing the 'ip nat outside' command.",
        "High",
    ),
    (
        "NAT_NO_OVERLOAD",
        re.compile(r"no 'overload' keyword present", re.IGNORECASE),
        "NAT is configured as static one-to-one translation without 'overload' (PAT), limiting simultaneous internet access.",
        "Medium",
    ),
    (
        "DUPLEX_MISMATCH",
        re.compile(r"half-duplex.{0,80}(?:CRC|late collisions)", re.IGNORECASE | re.DOTALL),
        "Possible duplex mismatch — half-duplex reported alongside CRC errors / late collisions.",
        "Low",
    ),
    (
        "OSPF_NO_NEIGHBOR",
        re.compile(r"no neighbors found", re.IGNORECASE),
        "OSPF has no neighbors — check area ID, network statements, hello/dead timers, or Layer 1/2 connectivity.",
        "High",
    ),
    (
        "MISSING_ROUTE",
        re.compile(r"is not in (?:the )?routing table", re.IGNORECASE),
        "Expected destination network is missing from the routing table.",
        "High",
    ),
    (
        "TRUNK_VLAN_PRUNED",
        re.compile(
            r"allowed on trunk:\s*([0-9,]+)\s*\|.*allowed on trunk:\s*([0-9,]+)",
            re.IGNORECASE | re.DOTALL,
        ),
        "Trunk allowed-VLAN lists differ between the two ends ({0} vs {1}) — a VLAN may be pruned on one side.",
        "High",
    ),
    (
        "HSRP_NO_DEFAULT_ROUTE",
        re.compile(r"no route to 0\.0\.0\.0/0", re.IGNORECASE),
        "HSRP standby/active router has no default route configured — failover would break internet access.",
        "High",
    ),
]


def run_checker(case_id: str, show_outputs: str, topology_note: str = "") -> CheckerResult:
    """Run all deterministic rules against a single case's evidence text."""
    combined_text = f"{show_outputs}\n{topology_note}"
    hits: List[RuleHit] = []

    for rule_id, pattern, message_template, severity in RULES:
        match = pattern.search(combined_text)
        if match:
            groups = match.groups() if match.groups() else (match.group(0),)
            try:
                message = message_template.format(*groups)
            except (IndexError, KeyError):
                message = message_template
            hits.append(
                RuleHit(
                    rule_id=rule_id,
                    message=message,
                    severity=severity,
                    matched_text=match.group(0),
                )
            )

    status = "ERRORS_DETECTED" if hits else "NO_ERRORS_DETECTED"
    return CheckerResult(case_id=case_id, status=status, hits=hits)


def load_cases(csv_path: Path = CASES_CSV):
    with open(csv_path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def check_case_by_id(case_id: str, csv_path: Path = CASES_CSV) -> CheckerResult:
    cases = load_cases(csv_path)
    for row in cases:
        if row["case_id"] == case_id:
            return run_checker(row["case_id"], row["show_outputs"], row.get("topology_note", ""))
    raise ValueError(f"Case {case_id} not found in {csv_path}")


def check_all(csv_path: Path = CASES_CSV) -> List[CheckerResult]:
    cases = load_cases(csv_path)
    return [
        run_checker(row["case_id"], row["show_outputs"], row.get("topology_note", ""))
        for row in cases
    ]


def _print_result(result: CheckerResult):
    print(f"\n=== {result.case_id} — {result.status} ===")
    if not result.hits:
        print("  No deterministic rule matched. Escalate to LLM inference.")
    for hit in result.hits:
        print(f"  [{hit.severity}] {hit.rule_id}: {hit.message}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NetSage AI deterministic rule checker")
    parser.add_argument("--case", help="Run checker against a single case_id, e.g. NET-001")
    parser.add_argument("--all", action="store_true", help="Run checker against all cases in cases.csv")
    args = parser.parse_args()

    if args.case:
        try:
            _print_result(check_case_by_id(args.case))
        except ValueError as e:
            print(str(e))
            sys.exit(1)
    elif args.all:
        results = check_all()
        detected = sum(1 for r in results if r.status == "ERRORS_DETECTED")
        for r in results:
            _print_result(r)
        print(f"\nSummary: {detected}/{len(results)} cases had deterministic rule hits.")
    else:
        parser.print_help()
