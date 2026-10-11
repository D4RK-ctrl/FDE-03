"""Policy-owned final fields and explicit failure handoffs for Architecture A."""
from __future__ import annotations

import os
import re
import logging

from pydantic import ValidationError

from src.agents import AgentAdvice
from src.contracts import EvidenceItem, ProcurementDecision, RunTelemetry
from src.evidence import PolicyResult
from src.telemetry import RunTelemetryCounter

logger = logging.getLogger(__name__)
REVIEW_SUBJECT = r"(?:(?:the|all|required|mandatory|human|security|privacy|legal|finance|financial|procurement|manager|department|head|cfo)\s+){0,6}(?:approvals?|reviews?|sign[- ]offs?)"
REVIEW_STATUS = r"(?:complete|completed|obtained|granted|secured|received|satisfied|done|approved|authorized|authorised)"
UNSAFE_RULES = {
    "bypass_controls": r"\b(bypass|override|waive|skip)\b|\b(ignore|disregard)\b.{0,50}\b(policy|rules|approvals|security)\b",
    "purchase_authorization": r"\b(purchase|request|spend|procurement)\s+(?:(?:is|has been|already|was)\s+)*(approved|authorized|authorised|completed)\b|\b(approved|authorized|authorised)\s+(?:the\s+)?(purchase|request|spend)\b|\bcfo[- ]approved\b",
    "purchase_action": r"\b(buy now|purchase now|proceed with (the )?purchase|place (the |an )?order|go ahead and buy|authorized to purchase)\b|\b(purchase|buy|proceed)\b.{0,25}\bwithout\b.{0,15}\bapprovals?\b",
    "waive_review": r"\bno\s+(?:(?:security|privacy|legal|human|further|additional|required)\s+){0,3}(approvals?|reviews?)\s+(?:(?:is|are)\s+)?(needed|required|necessary)\b|\b(approvals?|reviews?)\s+(?:(?:is|are)\s+)?(not required|unnecessary)\b",
    "completed_approval": rf"\b{REVIEW_SUBJECT}\s+(?:(?:is|are|was|were|has been|have been|already|now)\s+)*{REVIEW_STATUS}\b|\b(?:security|privacy|legal|finance|procurement|manager|cfo|department head)\s+(?:(?:team|department|reviewer)\s+)?(?:(?:has|have|had)\s+)?(?:already\s+)?(?:approved|authorized|authorised|signed off|cleared|granted approval)\b",
}
# Mask only explicit negative actions/statuses, not an entire sentence. Separate
# clauses ensure 'Do not buy now, but skip Security' still rejects the second act.
NEGATIVE_ACTION = re.compile(
    r"\b(do not|don't|must not|cannot|can not|never)\s+(?:purchase(?:\s+now)?|buy(?:\s+now)?|proceed with (?:the )?purchase|place (?:the |an )?order|bypass|skip|override|waive|ignore|disregard)\b(?:\s+without\s+(?:required\s+)?approvals?)?", re.I)
NEGATIVE_STATUS = re.compile(
    r"\b(purchase|request|spend)\s+(is not|has not been|cannot be|must not be)\s+(approved|authorized|authorised|completed)\b|\bno purchase (?:may proceed )?without (?:required )?approvals?\b", re.I)
FUTURE_REVIEW = re.compile(
    rf"\b(?:until|once|when|after|before)\s+{REVIEW_SUBJECT}\s+(?:(?:is|are|has been|have been)\s+)?{REVIEW_STATUS}\b", re.I)
# Mask only an imperative's review-status object, never the rest of a clause.
# 'Ensure ... completed and Finance has approved' still checks the second claim.
REQUEST_REVIEW = re.compile(
    rf"^\s*(?:please\s+)?(?:then\s+)?(?:ensure|confirm|verify|make sure)\s+(?:that\s+)?{REVIEW_SUBJECT}\s+(?:is|are)\s+{REVIEW_STATUS}\b", re.I)


def unsafe_condition(text: str) -> str | None:
    for clause in re.split(r"[.!?;,\n]|\b(?:but|however|instead|yet)\b", text, flags=re.I):
        clause = NEGATIVE_STATUS.sub("[negative status]", clause)
        clause = NEGATIVE_ACTION.sub("[negative action]", clause)
        clause = re.sub(r"\b(before|until)\s+(?:the\s+)?(purchase|request|spend)\s+(can be|is|may be)\s+(approved|authorized|authorised)\b", "[future authorization prerequisite]", clause, flags=re.I)
        # Completion inside an explicit future prerequisite is not a claim that
        # approvals have already occurred. Other unsafe acts still get checked.
        clause = FUTURE_REVIEW.sub("[future approval prerequisite]", clause)
        clause = REQUEST_REVIEW.sub("[request review completion]", clause)
        for name, pattern in UNSAFE_RULES.items():
            if re.search(pattern, clause, re.I):
                return name
    return None


def record_rejection(condition: str, field: str) -> None:
    diagnostic = {"condition": condition, "field": field}
    logger.info("decision_rejection condition=%s field=%s", condition, field,
                extra={"decision_diagnostic": diagnostic})


def grounded_evidence(items: list[EvidenceItem]) -> list[EvidenceItem]:
    # Only application-gathered findings enter the factual evidence trail.
    unique = {}
    for item in items:
        if item.reference:
            unique[(item.source, item.reference, item.finding)] = item.model_copy(deep=True)
    return list(unique.values())


def human_step(policy: PolicyResult) -> str:
    parts = ["Obtain human approval before any purchase."]
    if policy.required_approvals:
        parts.append("Route to: " + ", ".join(policy.required_approvals) + ".")
    if policy.missing_information:
        parts.append("Clarify: " + "; ".join(policy.missing_information) + ".")
    pending = list(dict.fromkeys(policy.blockers + policy.unresolved))
    if pending:
        parts.append("Resolve before authorization: " + "; ".join(pending) + ".")
    return " ".join(parts)


def safe_handoff(request_id: str, policy: PolicyResult, evidence: list[EvidenceItem],
                 error_code: str) -> ProcurementDecision:
    # Callers supply only controlled codes, never raw provider exceptions.
    return ProcurementDecision(
        request_id=request_id, recommendation="AI analysis unavailable — human review required",
        evidence=grounded_evidence(evidence), required_approvals=list(policy.required_approvals),
        missing_information=list(policy.missing_information),
        risk_flags=list(dict.fromkeys(policy.risk_flags + ["ai_analysis_unavailable", error_code])),
        next_step=f"AI advisory did not succeed ({error_code}). " + human_step(policy),
        human_review_required=True,
    )


def enforce_decision(request_id: str, advice: AgentAdvice | dict, policy: PolicyResult,
                     evidence: list[EvidenceItem]) -> ProcurementDecision:
    try:
        advice = AgentAdvice.model_validate(advice)
    except ValidationError:
        return safe_handoff(request_id, policy, evidence, "invalid_structured_output")
    if advice.request_id != request_id:
        return safe_handoff(request_id, policy, evidence, "request_identity_mismatch")
    known = {e.reference for e in evidence if e.reference}
    if any(ref not in known for ref in advice.evidence_references):
        return safe_handoff(request_id, policy, evidence, "unsupported_evidence_reference")
    key = os.getenv("GROQ_API_KEY", "").strip()
    for field in ("explanation", "next_step"):
        text = getattr(advice, field)
        condition = "credential_echo" if key and key in text else unsafe_condition(text)
        if condition:
            record_rejection(condition, field)
            return safe_handoff(request_id, policy, evidence, "unsafe_model_output")
    # Policy takes precedence over a model's favorable category. These categories
    # never describe final purchasing approval, even with no additional blockers.
    if policy.missing_information:
        category = "Request clarification"
    elif policy.blockers or policy.unresolved:
        category = "Required reviews before purchase"
    else:
        category = {"request_clarification": "Request clarification",
                    "review_required": "Human review required",
                    "consider_existing_tool": "Consider existing software",
                    "proceed_to_human_review": "Proceed to human approval review"}[advice.recommendation]
    result = ProcurementDecision(
        request_id=request_id,
        recommendation=category + ". AI advisory assessment: " + advice.explanation,
        evidence=grounded_evidence(evidence), required_approvals=list(policy.required_approvals),
        missing_information=list(policy.missing_information), risk_flags=list(policy.risk_flags),
        next_step=human_step(policy) + " Advisory follow-up: " + advice.next_step,
        human_review_required=True,
    )
    return ProcurementDecision.model_validate(result.model_dump())


def attach_telemetry(decision: ProcurementDecision, counter: RunTelemetryCounter) -> ProcurementDecision:
    decision.telemetry = RunTelemetry(llm_calls=counter.llm_calls, tool_calls=counter.tool_calls,
                                      tool_names=list(counter.tool_names))
    return decision
