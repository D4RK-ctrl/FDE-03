"""Deterministic implementation of procurement policy 2026.09, sections 1–11.

No recommendations here constitute purchasing approval. Free-form business notes
are used only for optional injection detection, never to grant exemptions.
"""
from __future__ import annotations

from datetime import date
import re

from src.contracts import EvidenceItem
from src.evidence import (
    REFERENCE_DATE, SENSITIVE_LEVELS, BudgetEvidence, CatalogEvidence,
    PolicyResult, RequestContext, VendorEvidence, money, normalized, request_gaps,
)

APPROVAL_ORDER = ("Manager", "Department Head", "Finance", "Procurement", "CFO", "Security", "Privacy", "Legal")
INJECTION = re.compile(r"ignore.{0,60}(rules|instructions|policy)|skip.{0,30}(approval|review)|bypass.{0,30}(control|policy)|cfo[- ]approved|approve it immediately", re.I | re.S)


def assessment_state(status: object, reviewed: object) -> str:
    """An age of 365 days is valid; missing/invalid/future is unverified."""
    if not isinstance(reviewed, str):
        return "unverified"
    try:
        reviewed_date = date.fromisoformat(reviewed)
    except ValueError:
        return "unverified"
    age = (REFERENCE_DATE - reviewed_date).days
    if age < 0:
        return "unverified"
    if age > 365 or normalized(status) == "expired":
        return "expired"
    return "current" if normalized(status) == "approved" else "unverified"


def _business_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _business_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _business_strings(child)


def validate_policy(context: RequestContext, budget: BudgetEvidence,
                    catalog: CatalogEvidence, vendor: VendorEvidence) -> PolicyResult:
    result = PolicyResult()
    approvals: set[str] = set()

    def rule(section: int, finding: str, *, approval: str | None = None,
             flag: str | None = None, blocker: str | None = None):
        result.evidence.append(EvidenceItem(source="procurement_policy", reference=f"data/procurement_policy.md#section-{section}", finding=finding))
        if approval:
            approvals.add(approval)
        if flag:
            result.risk_flags.append(flag)
        if blocker:
            result.blockers.append(blocker)

    def unresolved(message: str, section: int):
        result.unresolved.append(message)
        rule(section, message, blocker=message)

    req = context.request
    result.missing_information = list(dict.fromkeys(request_gaps(req) + context.missing_information))
    if not context.employee or not normalized(context.department):
        result.missing_information.append("verified requester/department")
    if context.status != "ok" or context.issues:
        unresolved("Request/employee context requires clarification: " + "; ".join(context.issues), 1)
    if result.missing_information:
        rule(1, "Material request information is missing or invalid; obtain clarification.", flag="missing_information", blocker="Clarify material request information before approval")

    cost = money(req.get("annual_cost_usd"))
    if cost is None:
        unresolved("Financial approval tier unresolved until annual cost is verified", 4)
    else:
        if cost <= 1000:
            approvals.add("Manager")
        elif cost <= 10000:
            approvals.update(("Department Head", "Procurement"))
        elif cost <= 25000:
            approvals.update(("Department Head", "Finance", "Procurement"))
        else:
            approvals.update(("Department Head", "Finance", "Procurement", "CFO"))
        rule(4, f"Annual spend {cost} determines minimum financial approvals; this is not purchasing authorization.")

    if (cost is None or budget.status != "ok" or budget.available is None
            or budget.department != context.department or budget.requested_cost != cost):
        unresolved("Budget comparison unverified; obtain verified department budget and cost", 2)
    elif cost > budget.available:
        rule(2, f"Annual cost {cost} exceeds available budget {budget.available}.", approval="Finance", flag="budget_insufficient", blocker="Finance budget-exception review required")
    else:
        rule(2, f"Annual cost {cost} is within available budget {budget.available}; approval is still required.")

    if catalog.possible_overlap or catalog.matches:
        rule(3, "Catalog candidates may overlap; validate use-case gap, scope and seat utilization. Category similarity does not establish equivalence.", flag="existing_tool_overlap")
    if catalog.status != "ok" or catalog.truncated:
        unresolved("Catalog/history evidence incomplete; verify alternatives manually", 3)

    level = normalized(req.get("data_access_level"))
    sensitive = level in SENSITIVE_LEVELS
    pii = level in {"employee_pii", "customer_pii"}
    integrations = req.get("requested_integrations")
    sensitive_integration = isinstance(integrations, list) and any(
        re.search(r"\bproduction\b|\bcloud[ _-]+account\b|\bgit[ _-]+repositor|\bsource[ _-]+code\b|\bcredentials\b|\bsecrets\b", normalized(x)) for x in integrations)
    sensitive = sensitive or sensitive_integration
    if sensitive or sensitive_integration:
        rule(5, "Requested sensitive data or production/cloud access requires use-case-specific Security review despite prior vendor approval.", approval="Security", flag="security_review_required", blocker="Security review required")
    if pii:
        rule(6, "Employee/customer PII requires Privacy review.", approval="Privacy", flag="privacy_review_required", blocker="Privacy review required")

    registry = vendor.registry
    risk = vendor.risk
    if normalized(vendor.vendor_name) != normalized(req.get("vendor_name")):
        registry, risk = None, None
        unresolved("Vendor evidence identity does not match request", 10)
    if registry and normalized(registry.get("vendor_name")) != normalized(req.get("vendor_name")):
        registry = None
        unresolved("Registry evidence identity does not match request", 10)
    if risk and normalized(risk.vendor_name) != normalized(req.get("vendor_name")):
        risk = None
        unresolved("API evidence identity does not match request", 10)
    states = [assessment_state(registry.get("security_status"), registry.get("security_review_date")) if registry else "unverified",
              assessment_state(risk.security_review_status, risk.last_review_date) if risk else "unverified"]
    if any(state != "current" for state in states):
        rule(5, f"Registry/API assessment states: {states}; current assessment not fully verified.", approval="Security", flag="security_review_required", blocker="Verify current vendor security assessment")
    if "expired" in states:
        rule(5, "At least one assessment is expired relative to 2026-09-30.", flag="vendor_review_expired")
    conflicts = list(vendor.conflicts)
    if registry and risk:
        if normalized(registry.get("security_status")) != risk.security_review_status:
            conflicts.append("security status")
        if (registry.get("security_review_date") or None) != risk.last_review_date:
            conflicts.append("assessment date")
    if conflicts:
        rule(5, "Conflicting vendor evidence: " + ", ".join(dict.fromkeys(conflicts)), approval="Security", flag="conflicting_vendor_evidence", blocker="Security must reconcile registry/API disagreement")
    if vendor.api_error or risk is None:
        rule(10, "Vendor-risk evidence unavailable or malformed; no favorable status inferred.", flag="vendor_risk_unavailable", blocker="Obtain vendor-risk evidence or human review")
    if vendor.issues:
        unresolved("Vendor evidence issues: " + "; ".join(vendor.issues), 10)

    if risk and risk.stores_data_outside_region and sensitive:
        rule(6, "Sensitive data may be stored outside the operating region, per vendor-risk evidence.", approval="Privacy", flag="privacy_review_required", blocker="Cross-region Privacy review required")
    elif risk is None or level not in SENSITIVE_LEVELS | {"none", "internal_documents", "internal_marketing"}:
        unresolved("Conditional cross-region Privacy/Legal review unresolved: data class or risk evidence missing", 6)

    if not registry:
        unresolved("Vendor newness and legal terms unverified; obtain registry/Legal confirmation", 7)
    else:
        procurement = normalized(registry.get("procurement_status"))
        if procurement == "new" and cost is not None and cost >= 10000:
            rule(7, "New vendor annual spend is at least $10,000.", approval="Legal", flag="legal_review_required", blocker="Legal onboarding review required")
        elif procurement == "new" and cost is None:
            unresolved("New-vendor spend-dependent Legal requirement unresolved until cost is verified", 7)
        elif procurement not in {"new", "approved"}:
            unresolved("Vendor procurement status unverified", 7)
        terms = normalized(registry.get("legal_terms_status"))
        if terms not in {"approved", "standard"}:
            rule(7, "Legal terms are not verified approved/standard.", approval="Legal", flag="legal_review_required", blocker="Legal terms review required")

    # A documented sensitive cross-region use is material. Generic vendor
    # personal-data capability alone does not prove this request processes PII.
    if risk and risk.stores_data_outside_region and sensitive:
        rule(7, "Material sensitive cross-region data-processing issue identified.", approval="Legal", flag="legal_review_required", blocker="Legal data-processing review required")
    elif pii and risk is None:
        unresolved("Material data-processing conditions require confirmation from Privacy/Legal", 7)
    elif pii:
        unresolved("Whether intended PII processing creates a material Legal issue requires Privacy/Legal confirmation; vendor capability alone does not settle terms", 7)

    business_data = [req, context.employee, context.manager, catalog.matches,
                     catalog.purchase_history, vendor.registry,
                     vendor.risk.model_dump() if vendor.risk else None]
    if any(INJECTION.search(text) for text in _business_strings(business_data)):
        rule(9, "Instruction-like business content ignored; mandatory rules and approval authority remain unchanged.", flag="prompt_injection_detected")
    rule(11, "Human authorization remains required; tools do not purchase, approve, alter budgets, or accept terms.")
    result.required_approvals = [name for name in APPROVAL_ORDER if name in approvals]
    result.risk_flags = list(dict.fromkeys(result.risk_flags))
    result.blockers = list(dict.fromkeys(result.blockers))
    result.unresolved = list(dict.fromkeys(result.unresolved))
    result.missing_information = list(dict.fromkeys(result.missing_information))
    return result
