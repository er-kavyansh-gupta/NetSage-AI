"""
src/app.py

NetSage AI — Streamlit Operations Dashboard.

Run locally with:
    streamlit run src/app.py

Provides:
  - Case selection from data/cases.csv
  - Symptom / topology / show-output display
  - Deterministic checker + LLM diagnosis (via engine.py)
  - Human-in-the-loop decision gate: Approve & Deploy / Edit Commands / Reject
  - Deployment/decision log written to docs/decision_log.csv
  - Summary dashboard: issue-type counts, severity mix, AI-vs-human agreement rate
"""

import sys
import json
import csv
from pathlib import Path
from datetime import datetime, timezone

import streamlit as st
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "src"))

import checker  # noqa: E402
import engine  # noqa: E402

CASES_CSV = BASE_DIR / "data" / "cases.csv"
DECISION_LOG = BASE_DIR / "docs" / "decision_log.csv"
AUDIT_LOG_MD = BASE_DIR / "docs" / "model_audit_log.md"

DECISION_LOG_FIELDS = [
    "timestamp", "case_id", "ai_root_cause", "ai_confidence", "ai_source",
    "decision", "reviewer_notes", "final_commands",
]

st.set_page_config(page_title="NetSage AI — Operations Dashboard", layout="wide")


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

@st.cache_data
def load_cases():
    with open(CASES_CSV, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def ensure_decision_log():
    if not DECISION_LOG.exists():
        DECISION_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(DECISION_LOG, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=DECISION_LOG_FIELDS)
            writer.writeheader()


def append_decision(row: dict):
    ensure_decision_log()
    with open(DECISION_LOG, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=DECISION_LOG_FIELDS)
        writer.writerow(row)


def load_decision_log() -> pd.DataFrame:
    ensure_decision_log()
    try:
        return pd.read_csv(DECISION_LOG)
    except pd.errors.EmptyDataError:
        return pd.DataFrame(columns=DECISION_LOG_FIELDS)


def append_audit_log_entry(case_id, ai_root_cause, decision, notes):
    """Append a human-readable line to docs/model_audit_log.md for Rejected/Edited decisions."""
    AUDIT_LOG_MD.parent.mkdir(parents=True, exist_ok=True)
    line = (
        f"\n### {case_id} — {decision} ({datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')})\n"
        f"- **AI root cause:** {ai_root_cause}\n"
        f"- **Reviewer notes:** {notes or '(none provided)'}\n"
    )
    with open(AUDIT_LOG_MD, "a", encoding="utf-8") as f:
        f.write(line)


# ---------------------------------------------------------------------------
# Sidebar navigation
# ---------------------------------------------------------------------------

st.sidebar.title("🛠️ NetSage AI")
page = st.sidebar.radio("Navigate", ["Case Diagnosis", "Dashboard Summary"])

cases = load_cases()
case_ids = [c["case_id"] for c in cases]

# ---------------------------------------------------------------------------
# Page 1: Case Diagnosis (the HITL workflow)
# ---------------------------------------------------------------------------

if page == "Case Diagnosis":
    st.title("Case Diagnosis & Human Review")

    selected_id = st.selectbox("Select Case ID", case_ids)
    case_row = next(c for c in cases if c["case_id"] == selected_id)

    col1, col2 = st.columns([1, 1])
    with col1:
        st.subheader("Case Details")
        st.markdown(f"**Symptom:** {case_row['symptom']}")
        st.markdown(f"**Topology note:** {case_row['topology_note']}")
        st.markdown(f"**Concept tag:** {case_row['concept_tag']} &nbsp;|&nbsp; "
                    f"**Severity:** {case_row['severity']} &nbsp;|&nbsp; "
                    f"**OSI layer (expected):** {case_row['osi_layer']}")
        st.code(case_row["show_outputs"], language="text")

    with col2:
        st.subheader("AI Diagnosis")
        run_diag = st.button("▶ Run Diagnosis (Checker + AI)", type="primary")

        diag_key = f"diag_{selected_id}"
        if run_diag:
            with st.spinner("Running deterministic checker and AI inference..."):
                st.session_state[diag_key] = engine.diagnose(selected_id)

        diagnosis = st.session_state.get(diag_key)

        if diagnosis:
            badge = {"high": "🟢", "medium": "🟡", "low": "🔴"}.get(diagnosis.get("confidence"), "⚪")
            st.markdown(f"**Source:** `{diagnosis.get('source')}`  |  "
                        f"**Confidence:** {badge} {diagnosis.get('confidence')}  |  "
                        f"**OSI layer:** {diagnosis.get('osi_layer')}")
            st.markdown(f"**Root cause:** {diagnosis.get('root_cause')}")
            st.markdown(f"**Evidence:** {diagnosis.get('evidence')}")
            st.markdown(f"**Next command:** `{diagnosis.get('next_command')}`")
            st.markdown("**Proposed fix steps:**")
            for step in diagnosis.get("fix_steps", []):
                st.markdown(f"- {step}")

            if diagnosis.get("checker_hits"):
                with st.expander("Deterministic checker hits"):
                    for hit in diagnosis["checker_hits"]:
                        st.markdown(f"- **[{hit['severity']}] {hit['rule_id']}:** {hit['message']}")

            st.divider()
            st.subheader("Human Review Decision")
            editable_commands = st.text_area(
                "Fix commands (editable before approval)",
                value="\n".join(diagnosis.get("fix_steps", [])),
                height=120,
            )
            notes = st.text_area("Reviewer notes (required for Reject/Edit)", height=80)

            b1, b2, b3 = st.columns(3)
            decision = None
            if b1.button("✅ Approve & Deploy"):
                decision = "Accepted"
            if b2.button("✏️ Edit Commands"):
                decision = "Edited"
            if b3.button("❌ Reject"):
                decision = "Rejected"

            if decision:
                append_decision({
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "case_id": selected_id,
                    "ai_root_cause": diagnosis.get("root_cause"),
                    "ai_confidence": diagnosis.get("confidence"),
                    "ai_source": diagnosis.get("source"),
                    "decision": decision,
                    "reviewer_notes": notes,
                    "final_commands": editable_commands.replace("\n", " | "),
                })
                if decision in ("Edited", "Rejected"):
                    append_audit_log_entry(selected_id, diagnosis.get("root_cause"), decision, notes)
                st.success(f"Decision logged: {decision}")
        else:
            st.info("Click **Run Diagnosis** to generate the AI diagnostic output.")

# ---------------------------------------------------------------------------
# Page 2: Dashboard Summary
# ---------------------------------------------------------------------------

else:
    st.title("Dashboard Summary")

    df_cases = pd.DataFrame(cases)
    df_log = load_decision_log()

    c1, c2, c3 = st.columns(3)
    c1.metric("Total cases", len(df_cases))
    c2.metric("Reviewed decisions logged", len(df_log))
    if len(df_log):
        agreement_rate = (df_log["decision"] == "Accepted").mean() * 100
        c3.metric("AI/human agreement rate", f"{agreement_rate:.1f}%")
    else:
        c3.metric("AI/human agreement rate", "N/A")

    st.subheader("Issue types (concept_tag)")
    st.bar_chart(df_cases["concept_tag"].value_counts())

    st.subheader("Severity mix")
    st.bar_chart(df_cases["severity"].value_counts())

    st.subheader("OSI layer distribution")
    st.bar_chart(df_cases["osi_layer"].value_counts())

    if len(df_log):
        st.subheader("Reviewer decisions over time")
        st.dataframe(df_log, use_container_width=True)

        st.subheader("Decision breakdown")
        st.bar_chart(df_log["decision"].value_counts())
    else:
        st.info("No human review decisions logged yet — go to **Case Diagnosis** and review a case.")
