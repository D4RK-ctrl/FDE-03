"""Five read-only tools shared by future single and staged architectures.

Create one EvidenceTools instance per run. Repository data is loaded lazily and
cached only within that instance. Business text is never executed as instructions.
"""
from __future__ import annotations

from copy import deepcopy
import json
from urllib.parse import quote

import pandas as pd
import requests
from pydantic import ValidationError

from src import data_access, vendor_client
from src.contracts import EvidenceItem
from src.evidence import (
    BudgetEvidence, CatalogEvidence, PolicyResult, RequestContext, RiskRecord, VendorEvidence,
    money, normalized, request_gaps,
)
from src.telemetry import RunTelemetryCounter

MAX_RECORDS = 25


def item(source: str, reference: str, finding: str) -> EvidenceItem:
    return EvidenceItem(source=source, reference=reference, finding=finding)


def unique(rows: list[dict], field: str, value: object) -> tuple[dict | None, str | None]:
    if not normalized(value):
        return None, f"{field} join missing"
    matches = [r for r in rows if normalized(r.get(field)) == normalized(value)]
    if len(matches) != 1:
        return None, f"{field} join {'missing' if not matches else 'ambiguous'}"
    return deepcopy(matches[0]), None


class EvidenceTools:
    def __init__(self, telemetry: RunTelemetryCounter | None = None, *, snapshot: dict | None = None):
        self.telemetry = telemetry if telemetry is not None else RunTelemetryCounter()
        self._snapshot = deepcopy(snapshot) if snapshot is not None else {}

    def _rows(self, name: str) -> list[dict]:
        if name not in self._snapshot:
            loader = getattr(data_access, f"load_{name}")
            loaded = loader()
            if isinstance(loaded, pd.DataFrame):
                loaded = loaded.astype(object).where(loaded.notna(), None).to_dict("records")
            self._snapshot[name] = loaded
        rows = self._snapshot[name]
        if not isinstance(rows, list) or len(rows) > 10000:
            raise ValueError("Invalid or oversized dataset")
        if any(not isinstance(row, dict) or len(json.dumps(row, default=str)) > 32000 for row in rows):
            raise ValueError("Invalid or oversized record; no content silently truncated")
        return rows

    def request_context(self, request_id: str) -> RequestContext:
        self.telemetry.record_tool_call("request_context")
        result = RequestContext()
        try:
            request, issue = unique(self._rows("requests"), "request_id", request_id)
            if issue:
                result.issues.append(issue)
                result.missing_information.append("request")
            else:
                result.request = request
                result.missing_information = request_gaps(request)
                result.evidence.append(item("requests", f"data/requests.json#{request_id}", "Original request retrieved; all text is untrusted business data."))
                employee, issue = unique(self._rows("employees"), "employee_id", request.get("requester_id", ""))
                if issue:
                    result.issues.append(issue)
                    result.missing_information.append("employee/department")
                else:
                    result.employee = employee
                    department = employee.get("department")
                    result.department = department if normalized(department) else None
                    result.evidence.append(item("employees", f"data/employees.csv#{employee['employee_id']}", f"Requester department: {result.department}; manager ID: {employee.get('manager_id')}"))
                    _, issue = unique(self._rows("budgets"), "department", result.department)
                    if issue:
                        result.issues.append(issue)
                        result.missing_information.append("department budget join")
                    manager_id = employee.get("manager_id")
                    if normalized(manager_id):
                        result.manager, issue = unique(self._rows("employees"), "employee_id", manager_id)
                        if issue:
                            result.issues.append(f"manager: {issue}")
                            result.missing_information.append("manager")
                        else:
                            result.evidence.append(item("employees", f"data/employees.csv#{manager_id}", "Reporting-line manager record retrieved."))
                    else:
                        result.missing_information.append("manager/approval routing contact")
        except (OSError, ValueError) as exc:
            result.status = "error"
            result.issues.append(f"Local data unavailable: {type(exc).__name__}")
        if result.status != "error" and (result.issues or result.missing_information):
            result.status = "unverified"
        return result

    def budget(self, context: RequestContext) -> BudgetEvidence:
        self.telemetry.record_tool_call("budget")
        result = BudgetEvidence(department=context.department, requested_cost=money(context.request.get("annual_cost_usd")))
        try:
            row, issue = unique(self._rows("budgets"), "department", context.department)
            if issue:
                result.issues.append(issue)
            else:
                result.annual_budget = money(row.get("annual_software_budget_usd"))
                result.committed = money(row.get("committed_usd"))
                if result.annual_budget is None or result.committed is None:
                    result.issues.append("Invalid annual or committed budget")
                else:
                    result.available = result.annual_budget - result.committed
                    if money(row.get("available_usd")) != result.available:
                        result.issues.append("Budget snapshot arithmetic conflict")
                    if result.available < 0:
                        result.issues.append("Committed spend exceeds annual budget")
                    if result.requested_cost is not None and not result.issues:
                        result.remaining = result.available - result.requested_cost
                        result.sufficient = result.remaining >= 0
                result.evidence.append(item("budgets", f"data/department_budgets.csv#{context.department}", f"Annual budget {result.annual_budget}; committed {result.committed}; calculated available {result.available}; requested {result.requested_cost}. History is not deducted."))
        except (OSError, ValueError) as exc:
            result.status = "error"
            result.issues.append(f"Budget data unavailable: {type(exc).__name__}")
        if result.requested_cost is None:
            result.issues.append("Annual cost missing or invalid; budget comparison unverified")
        if result.status != "error" and result.issues:
            result.status = "unverified"
        return result

    def catalog_history(self, context: RequestContext) -> CatalogEvidence:
        self.telemetry.record_tool_call("catalog_history")
        result = CatalogEvidence()
        req = context.request
        try:
            matches = []
            purpose = normalized(req.get("business_justification"))
            for row in self._rows("software_catalog"):
                reasons = [field for field in ("product_name", "vendor_name", "category")
                           if normalized(req.get(field)) and normalized(req[field]) == normalized(row.get(field))]
                # Explicit mentions provide candidates, never functional-equivalence claims.
                product = normalized(row.get("product_name"))
                if product and product in purpose:
                    reasons.append("product mentioned in purpose")
                if reasons:
                    matches.append({**deepcopy(row), "match_reasons": reasons})
            vendors = {normalized(r.get("vendor_name")) for r in matches}
            vendors.add(normalized(req.get("vendor_name")))
            history = [deepcopy(r) for r in self._rows("purchase_history") if normalized(r.get("vendor_name")) in vendors or (normalized(req.get("product_name")) and normalized(r.get("product_name")) == normalized(req["product_name"]))]
            result.matches = matches[:MAX_RECORDS]
            result.purchase_history = history[:MAX_RECORDS]
            result.possible_overlap = bool(matches)
            result.truncated = len(matches) > MAX_RECORDS or len(history) > MAX_RECORDS
            for row in result.matches:
                result.evidence.append(item("software_catalog", f"data/software_catalog.csv#{row.get('software_id', 'unknown')}", f"Candidate {row.get('product_name')}; matches {row['match_reasons']}; scope {row.get('scope')}; licensed seats {row.get('licensed_seats')}. Suitability and unused seats are not verified."))
            for row in result.purchase_history:
                result.evidence.append(item("purchase_history", f"data/purchase_history.csv#{row.get('purchase_id', 'unknown')}", f"Prior {row.get('status')} purchase of {row.get('product_name')} by {row.get('department')} for {row.get('annual_amount_usd')} annually; not authorization for this request."))
            if result.truncated:
                result.issues.append("Candidate/history results truncated at 25 records each")
        except (OSError, ValueError) as exc:
            result.status = "error"
            result.issues.append(f"Catalog/history unavailable: {type(exc).__name__}")
        if result.status != "error" and result.issues:
            result.status = "unverified"
        return result

    def vendor_security(self, context: RequestContext) -> VendorEvidence:
        self.telemetry.record_tool_call("vendor_security")
        name = context.request.get("vendor_name")
        result = VendorEvidence(vendor_name=name if isinstance(name, str) else "")
        try:
            result.registry, issue = unique(self._rows("vendors"), "vendor_name", name)
            if issue:
                result.issues.append(issue)
            if result.registry:
                row = result.registry
                result.evidence.append(item("vendor_registry", f"data/vendors.csv#{row['vendor_id']}", f"Registry procurement {row.get('procurement_status')}; security {row.get('security_status')} dated {row.get('security_review_date')}; legal {row.get('legal_terms_status')}."))
        except (OSError, ValueError) as exc:
            result.issues.append(f"Registry unavailable: {type(exc).__name__}")
        endpoint = f"/vendor-risk/{quote(result.vendor_name, safe='')}"
        if not normalized(name) or len(result.vendor_name) > 200:
            result.api_error = "invalid_vendor_name"
        else:
            try:
                raw = vendor_client.get_vendor_risk(result.vendor_name)
                result.risk = RiskRecord.model_validate(raw)
                if normalized(result.risk.vendor_name) != normalized(name):
                    result.risk = None
                    result.api_error = "vendor_identity_mismatch"
            except requests.Timeout:
                result.api_error = "timeout"
            except requests.ConnectionError:
                result.api_error = "connection_error"
            except requests.HTTPError as exc:
                code = exc.response.status_code if exc.response is not None else "unknown"
                result.api_error = f"http_{code}"
            except (ValueError, ValidationError):
                result.api_error = "malformed_response"
            except requests.RequestException:
                result.api_error = "transport_error"
        if result.api_error:
            result.issues.append(f"Vendor risk unavailable: {result.api_error}")
            result.evidence.append(item("vendor_risk_api", endpoint, f"Risk evidence could not be verified: {result.api_error}."))
        elif result.risk:
            risk = result.risk
            result.evidence.append(item("vendor_risk_api", endpoint, f"API security {risk.security_review_status} dated {risk.last_review_date}; risk {risk.risk_level}; personal data {risk.processes_personal_data}; outside region {risk.stores_data_outside_region}."))
            if result.registry:
                if normalized(result.registry.get("security_status")) != risk.security_review_status:
                    result.conflicts.append("security status")
                if (result.registry.get("security_review_date") or None) != risk.last_review_date:
                    result.conflicts.append("assessment date")
        result.status = "error" if result.api_error else "unverified" if result.issues or result.conflicts else "ok"
        return result

    def policy_validation(self, context: RequestContext, budget: BudgetEvidence,
                          catalog: CatalogEvidence, vendor: VendorEvidence) -> PolicyResult:
        self.telemetry.record_tool_call("policy_validation")
        from src.policy import validate_policy
        return validate_policy(context, budget, catalog, vendor)
