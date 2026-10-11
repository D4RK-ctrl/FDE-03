from __future__ import annotations

import streamlit as st
from src.data_access import load_employees, load_requests
from src.solution import handle_request
from src.ui_presentation import (
    ARCHITECTURES, analysis_label, approval_rows, clear_analysis, group_evidence,
    human_review_label, request_overview, risk_explanation, run_analysis,
    sync_selection, telemetry_values,
)


def render_decision(decision, architecture: str, latency: float) -> None:
    st.divider()
    with st.container(border=True):
        st.subheader("Executive recommendation")
        st.caption(analysis_label(decision))
        st.text(decision.recommendation)
        st.caption("Human review")
        st.text(human_review_label(decision.human_review_required))
    with st.container(border=True):
        st.subheader("Recommended next action")
        st.text(decision.next_step)
    approvals, uncertainties = st.columns(2, gap="large")
    with approvals:
        st.subheader("Approval requirements")
        st.caption("These are required/pending approvals, not approvals already granted.")
        rows = approval_rows(decision)
        if rows:
            st.dataframe(rows, hide_index=True, width="stretch")
        else:
            st.info("No specific approval names were returned. Purchasing authorization remains separate.")
        st.subheader("Human escalation")
        st.text(human_review_label(decision.human_review_required))
        st.caption("Use the recommended next action to resolve missing information and arrange required reviews.")
    with uncertainties:
        st.subheader("Missing information")
        if decision.missing_information:
            for item in decision.missing_information:
                st.text(item)
        else:
            st.caption("No missing information was reported.")
        st.subheader("Risk flags")
        if decision.risk_flags:
            for flag in decision.risk_flags:
                st.warning(risk_explanation(flag))
                st.text(flag)
        else:
            st.caption("No risk flags were reported. This does not constitute purchasing approval.")
    st.subheader("Supporting evidence")
    st.caption("Source findings are shown as returned by the application.")
    groups = group_evidence(decision)
    if not groups:
        st.warning("No supporting evidence was returned; request a human evidence review.")
    for name, records in groups.items():
        with st.expander(f"{name} ({len(records)})"):
            st.dataframe(records, hide_index=True, width="stretch")
    st.subheader("Execution metrics")
    values = telemetry_values(decision)
    columns = st.columns(3)
    columns[0].metric("LLM HTTP attempts", values["LLM attempts"])
    columns[1].metric("Tool calls", values["Tool calls"])
    columns[2].metric("Execution latency", f"{latency:.2f} s")
    st.caption("Architecture used")
    st.text(ARCHITECTURES[architecture])
    st.caption("Tools invoked")
    st.text(values["Tool names"])
    st.caption("Latency measures this application's handle_request call, including transport retries. "
               "It does not include evaluation pacing delays.")


def main() -> None:
    st.set_page_config(page_title="AI Procurement Request Copilot", layout="wide")
    st.title("AI Procurement Request Copilot")
    st.caption("Evidence-backed procurement recommendations with human oversight.")
    st.info("Recommendations are advisory and never constitute purchasing approval. "
            "Authorized humans make purchasing and sensitive approval decisions.")
    try:
        requests = load_requests()
        frame = load_employees()
        employees = frame.astype(object).where(frame.notna(), None).to_dict("records")
        by_id = {r["request_id"]: r for r in requests}
    except (OSError, ValueError, KeyError):
        st.error("Request data could not be loaded. Ask the application operator to check the local datasets.")
        return
    if not by_id:
        st.warning("No requests are available. Ask the application operator to check the request dataset.")
        return
    with st.sidebar:
        st.header("Request analysis")
        request_id = st.selectbox("Request", list(by_id), key="selected_request",
                                  format_func=lambda rid: f"{rid} — {by_id[rid].get('product_name') or 'Missing product'}")
        architecture = st.radio("Architecture", list(ARCHITECTURES),
                                format_func=ARCHITECTURES.__getitem__, key="selected_architecture")
        st.caption("Model")
        st.text("openai/gpt-oss-120b")
        sync_selection(st.session_state, request_id, architecture)
        has_analysis = "analysis_result" in st.session_state or "analysis_error" in st.session_state
        running = st.session_state.get("analysis_running", False)
        analyze = st.button("Analyze Request", key="analyze", type="primary", width="stretch",
                            disabled=has_analysis or running)
        clear = st.button("Clear Results", key="clear", width="stretch", disabled=not has_analysis or running)
        st.caption("Analysis runs only when requested. Clear results to analyze this selection again.")
    if clear:
        clear_analysis(st.session_state)
        st.rerun()
    st.subheader("Request overview")
    columns = st.columns(3, gap="large")
    for index, (label, value) in enumerate(request_overview(by_id[request_id], employees).items()):
        with columns[index % 3]:
            st.caption(label)
            st.text(value)
    if analyze:
        with st.spinner("Gathering evidence and preparing an advisory recommendation…"):
            run_analysis(st.session_state, request_id, architecture, handle_request)
        st.rerun()
    if "analysis_error" in st.session_state:
        st.error(st.session_state["analysis_error"])
    elif "analysis_result" in st.session_state:
        render_decision(st.session_state["analysis_result"], architecture, st.session_state["analysis_latency"])
    else:
        st.info("Review the request details, choose an architecture, then select Analyze Request.")


if __name__ == "__main__":
    main()
