"""UI helpers. No procurement policy decisions or external calls."""
from decimal import Decimal, InvalidOperation
from time import perf_counter

from src.contracts import ProcurementDecision

ARCHITECTURES = {"single": "Single Agent — Architecture A", "staged": "Staged Two-Agent — Architecture B"}
RISK_EXPLANATIONS = {
    "existing_tool_overlap": "Existing software may overlap with the requested use case.",
    "budget_insufficient": "Available budget is insufficient; budget-exception review is needed.",
    "security_review_required": "Security review is required.",
    "privacy_review_required": "Privacy review is required.",
    "legal_review_required": "Legal review is required.",
    "vendor_review_expired": "The vendor assessment needs renewal.",
    "conflicting_vendor_evidence": "Conflicting vendor evidence needs human resolution.",
    "vendor_risk_unavailable": "Vendor risk evidence was unavailable and cannot establish clearance.",
    "prompt_injection_detected": "Untrusted content contained instructions that require scrutiny.",
    "missing_information": "Material information is missing or unverified.",
    "ai_analysis_unavailable": "AI analysis was unavailable or rejected; a human handoff is shown.",
    "unsafe_model_output": "The safety validator rejected AI output.",
    "rate_limited": "The provider rate limit prevented AI analysis.",
    "timeout": "AI analysis timed out.",
    "connection_error": "The provider connection failed.",
    "authentication_error": "Provider authentication failed; contact the application operator.",
    "missing_api_key": "Provider credentials are not configured; contact the application operator.",
    "request_rejected": "The provider rejected the analysis request.",
    "service_unavailable": "The provider service was unavailable.",
    "unsupported_evidence_reference": "AI output included an unverified evidence reference.",
}


def display_value(value):
    return "Missing" if value is None or (isinstance(value, str) and not value.strip()) else str(value)


def cost_label(value):
    if value is None or value == "":
        return "Missing"
    try:
        if isinstance(value, bool):
            raise ValueError
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0:
            raise ValueError
        return f"${amount:,.2f}"
    except (InvalidOperation, ValueError):
        return "Invalid value"


def request_overview(request, employees):
    requester = request.get("requester_id")
    matches = [e for e in employees if requester is not None
               and str(e.get("employee_id", "")).casefold() == str(requester).casefold()]
    employee = matches[0] if len(matches) == 1 else {}
    identity = display_value(requester)
    if employee.get("name"):
        identity = f"{employee['name']} ({identity})"
    elif len(matches) > 1:
        identity += " — ambiguous employee records"
    integrations = request.get("requested_integrations")
    integration_label = ("Missing" if integrations is None else "None requested" if integrations == []
                         else ", ".join(str(item) for item in integrations)
                         if isinstance(integrations, list) else "Invalid value")
    fields = {"Request ID": "request_id", "Product": "product_name", "Vendor": "vendor_name"}
    overview = {label: display_value(request.get(key)) for label, key in fields.items()}
    overview.update({"Requester": identity, "Department": display_value(employee.get("department")),
                     "Annual cost": cost_label(request.get("annual_cost_usd")),
                     "Number of users": display_value(request.get("user_count")),
                     "Business justification": display_value(request.get("business_justification")),
                     "Data access": display_value(request.get("data_access_level")),
                     "Requested integrations": integration_label, "Urgency": display_value(request.get("urgency"))})
    return overview


def group_evidence(decision):
    names = {"requests": "Request and employee", "employees": "Request and employee", "budgets": "Budget",
             "software_catalog": "Software catalog and history", "purchase_history": "Software catalog and history",
             "vendor_registry": "Vendor and security", "vendor_risk_api": "Vendor and security",
             "procurement_policy": "Procurement policy"}
    groups = {}
    for item in decision.evidence:
        groups.setdefault(names.get(item.source, "Other evidence"), []).append({
            "Source": item.source, "Finding": item.finding,
            "Reference": item.reference if item.reference is not None else "Not supplied"})
    return groups


def risk_explanation(flag):
    stage = ""
    for prefix, label in (("analyst_", "Analyst: "), ("reviewer_", "Reviewer: ")):
        if flag.startswith(prefix):
            stage, flag = label, flag[len(prefix):]
            break
    return stage + RISK_EXPLANATIONS.get(flag, "Additional concern returned by the analysis; review with procurement.")


def analysis_label(decision):
    return ("AI analysis unavailable — human handoff" if "ai_analysis_unavailable" in decision.risk_flags
            else "AI analysis accepted")


def human_review_label(required):
    return "Required — pending human review" if required else "No additional review flagged — purchasing approval is separate"


def approval_rows(decision):
    return [{"Required approval": name, "Status": "Pending human authorization"} for name in decision.required_approvals]


def telemetry_values(decision):
    telemetry = decision.telemetry
    return {"LLM attempts": str(telemetry.llm_calls) if telemetry and telemetry.llm_calls is not None else "Not reported",
            "Tool calls": str(telemetry.tool_calls) if telemetry and telemetry.tool_calls is not None else "Not reported",
            "Tool names": ", ".join(telemetry.tool_names) if telemetry and telemetry.tool_names else "Not reported"}


def clear_analysis(state):
    for key in ("analysis_result", "analysis_latency", "analysis_error"):
        state.pop(key, None)


def sync_selection(state, request_id, architecture):
    selection = (request_id, architecture)
    if state.get("analysis_selection") != selection:
        clear_analysis(state)
        state["analysis_selection"] = selection


def run_analysis(state, request_id, architecture, handler, clock=perf_counter):
    """One explicit action; errors stay visible without exposing exception content."""
    sync_selection(state, request_id, architecture)
    if state.get("analysis_running") or "analysis_result" in state or "analysis_error" in state:
        return False
    state["analysis_running"] = True
    started = clock()
    try:
        result = handler(request_id, architecture=architecture)
        if not isinstance(result, ProcurementDecision) or result.request_id != request_id:
            raise ValueError("Decision does not match selection")
        state["analysis_result"] = result.model_copy(deep=True)
    except Exception:
        # Visible UI boundary failure, not a fabricated decision or raw provider error.
        state["analysis_error"] = ("Analysis could not be displayed safely. Clear results to retry, or contact "
                                   "the application operator. No purchasing authorization has been granted.")
    finally:
        state["analysis_latency"] = max(0.0, clock() - started)
        state["analysis_running"] = False
    return True
