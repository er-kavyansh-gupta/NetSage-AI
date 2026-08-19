# NetSage AI — Automated Network Diagnostic Platform

An AI-assisted troubleshooting helper for Cisco-style / Packet Tracer lab
networks. Combines a deterministic rule checker with LLM-based diagnosis,
gated behind a mandatory human review step, exactly per the project brief.

## 1. Folder structure

```
NetSage_AI/
├── data/
│   └── cases.csv                  # 30 troubleshooting cases (deliverable)
├── prompts/
│   └── diagnose_prompt.md         # System prompt + few-shot examples (deliverable)
├── src/
│   ├── checker.py                 # Deterministic rule-based validator
│   ├── engine.py                  # Orchestrator: checker + LLM -> structured JSON
│   └── app.py                     # Streamlit dashboard (the HITL gate)
├── docs/
│   ├── model_audit_log.md         # Responsible AI log — 5+ corrected cases (deliverable)
│   └── decision_log.csv           # Machine-readable log, auto-appended by app.py
├── system_config.json             # Model name, thresholds, paths
├── requirements.txt
└── README.md
```

## 2. Run it locally

**Step 1 — install dependencies** (Python 3.10+):
```bash
cd NetSage_AI
pip install -r requirements.txt
```

**Step 2 — (optional but recommended) set your Anthropic API key**, so
the dashboard uses real LLM inference instead of the deterministic-only
fallback:
```bash
export ANTHROPIC_API_KEY="sk-ant-...."      # macOS/Linux
setx ANTHROPIC_API_KEY "sk-ant-...."        # Windows (new terminal after)
```
Without a key set, the app still runs end-to-end — `engine.py` falls
back to a deterministic-only diagnosis so you can demo the full HITL
workflow offline (useful for the demo video if you don't want to expose
API usage on camera).

**Step 3 — launch the dashboard:**
```bash
streamlit run src/app.py
```
This opens `http://localhost:8501` in your browser. Use the sidebar to
switch between **Case Diagnosis** (the main HITL workflow — pick a case,
run diagnosis, Approve/Edit/Reject) and **Dashboard Summary** (issue-type
counts, severity mix, AI-vs-human agreement rate).

**Command-line tools** (useful for grading / the demo video):
```bash
python src/checker.py --case NET-001      # single case
python src/checker.py --all               # all 30 cases
python src/engine.py --case NET-001       # full diagnosis JSON for one case
```

## 3. Mapping this repo to the deliverables checklist

| Deliverable | File |
|---|---|
| cases.csv | `data/cases.csv` (30 cases, all required columns) |
| Prompt files | `prompts/diagnose_prompt.md` |
| Python checker | `src/checker.py` (run with `--all` for sample output) |
| Dashboard | `src/app.py` → **Dashboard Summary** page |
| Responsible AI log | `docs/model_audit_log.md` (5 seeded corrected cases; grows automatically as you review more cases live) |
| Demo video | Record yourself: pick a broken case (e.g. NET-001) → Run Diagnosis → review evidence → Approve/Edit/Reject → show it logged in `docs/decision_log.csv` and reflected on the Dashboard Summary page |

## 4. Notes on the Responsible AI design

- Every diagnosis carries `"requires_human_review": true` — the code
  never auto-applies a fix.
- The deterministic checker (`checker.py`) runs first and is fully
  explainable (regex-based, no hallucination risk); its hits are merged
  into the final diagnosis as high-confidence evidence.
- The dashboard only offers **Approve & Deploy / Edit Commands /
  Reject** — there is no "auto-apply" path.
- `docs/model_audit_log.md` is the audit trail showing where the AI was
  wrong and why, per the project's Responsible AI check.
