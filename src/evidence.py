"""Internal evidence models; the public decision contract remains unchanged."""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, Field

from src.contracts import EvidenceItem

REFERENCE_DATE = date(2026, 9, 30)
DATA_LEVELS = frozenset({
    "none", "internal_documents", "internal_marketing", "source_code",
    "production_telemetry", "confidential_documents", "employee_pii",
    "customer_pii", "credentials", "secrets", "credentials/secrets",
    "production", "cloud_account",
})
SENSITIVE_LEVELS = DATA_LEVELS - {"none", "internal_documents", "internal_marketing"}


def normalized(value: Any) -> str:
    return value.strip().casefold() if isinstance(value, str) else ""


def money(value: Any) -> Decimal | None:
    """Reject missing, boolean, nonfinite, negative, and sub-cent amounts."""
    if value is None or isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        return None
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        return None
    if not amount.is_finite() or amount < 0 or amount.as_tuple().exponent < -2:
        return None
    return amount


def request_gaps(request: dict) -> list[str]:
    gaps = []
    for field in ("requester_id", "product_name", "vendor_name", "business_justification"):
        if not normalized(request.get(field)):
            gaps.append(field)
    if money(request.get("annual_cost_usd")) is None:
        gaps.append("annual_cost_usd (missing or invalid annual cost)")
    users = request.get("user_count")
    if isinstance(users, bool) or not isinstance(users, int) or users <= 0:
        gaps.append("user_count (positive integer users/licenses required)")
    if normalized(request.get("data_access_level")) not in DATA_LEVELS:
        gaps.append("data_access_level (missing or unrecognized data class)")
    integrations = request.get("requested_integrations")
    if not isinstance(integrations, list) or any(not normalized(x) for x in integrations):
        gaps.append("requested_integrations (list required; [] explicitly means none)")
    return gaps


class ToolEvidence(BaseModel):
    status: Literal["ok", "unverified", "error"] = "ok"
    evidence: list[EvidenceItem] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)


class RequestContext(ToolEvidence):
    request: dict[str, Any] = Field(default_factory=dict)
    employee: dict[str, Any] | None = None
    manager: dict[str, Any] | None = None
    department: str | None = None
    missing_information: list[str] = Field(default_factory=list)


class BudgetEvidence(ToolEvidence):
    department: str | None = None
    annual_budget: Decimal | None = None
    committed: Decimal | None = None
    available: Decimal | None = None
    requested_cost: Decimal | None = None
    remaining: Decimal | None = None
    sufficient: bool | None = None


class CatalogEvidence(ToolEvidence):
    matches: list[dict[str, Any]] = Field(default_factory=list)
    purchase_history: list[dict[str, Any]] = Field(default_factory=list)
    possible_overlap: bool = False
    truncated: bool = False


class RiskRecord(BaseModel):
    vendor_name: str = Field(strict=True, min_length=1, max_length=200)
    risk_level: Literal["low", "medium", "high", "unknown"]
    security_review_status: Literal["approved", "expired", "not_completed", "pending", "unknown"]
    last_review_date: str | None = Field(max_length=40)
    processes_personal_data: bool = Field(strict=True)
    stores_data_outside_region: bool = Field(strict=True)
    notes: str = Field(default="", strict=True, max_length=4000)


class VendorEvidence(ToolEvidence):
    vendor_name: str = ""
    registry: dict[str, Any] | None = None
    risk: RiskRecord | None = None
    api_error: str | None = None
    conflicts: list[str] = Field(default_factory=list)


class PolicyResult(BaseModel):
    policy_version: str = "2026.09"
    reference_date: date = REFERENCE_DATE
    required_approvals: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    human_review_required: Literal[True] = True
