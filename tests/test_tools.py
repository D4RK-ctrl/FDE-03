from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import unittest
from unittest.mock import Mock, patch

import requests

from src import data_access
from src.tools import EvidenceTools
from src.telemetry import RunTelemetryCounter


def snapshot():
    return {
        "requests": [{"request_id": "TEST", "requester_id": "E1", "vendor_name": "Acme",
                      "product_name": "Writer", "category": "Writing", "annual_cost_usd": 1000,
                      "user_count": 2, "business_justification": "Write documents",
                      "data_access_level": "none", "requested_integrations": []}],
        "employees": [{"employee_id": "E1", "department": "Ops", "manager_id": "E2"},
                      {"employee_id": "E2", "department": "Ops", "manager_id": None}],
        "budgets": [{"department": "Ops", "annual_software_budget_usd": 10000,
                     "committed_usd": 9000, "available_usd": 1000}],
        "vendors": [{"vendor_id": "V1", "vendor_name": "Acme", "procurement_status": "Approved",
                     "security_status": "Approved", "security_review_date": "2026-06-01",
                     "legal_terms_status": "Approved", "notes": "Standard"}],
        "software_catalog": [{"software_id": "SW1", "product_name": "Writer", "vendor_name": "Acme",
                              "category": "Writing", "scope": "Company-wide", "licensed_seats": 10,
                              "status": "Approved", "notes": "Existing tool"}],
        "purchase_history": [{"purchase_id": "PO1", "vendor_name": "Acme", "product_name": "Writer",
                              "department": "Ops", "annual_amount_usd": 9000, "status": "Approved"}],
    }


def risk_payload():
    return {"vendor_name": "Acme", "risk_level": "low", "security_review_status": "approved",
            "last_review_date": "2026-06-01", "processes_personal_data": False,
            "stores_data_outside_region": False, "notes": "Current"}


class ToolsTests(unittest.TestCase):
    def setUp(self):
        self.data = snapshot()
        self.telemetry = RunTelemetryCounter()
        self.transport = patch("src.vendor_client.requests.get")
        self.http = self.transport.start()
        self.addCleanup(self.transport.stop)
        self.response = Mock()
        self.response.json.return_value = risk_payload()
        self.http.return_value = self.response

    def tools(self):
        return EvidenceTools(self.telemetry, snapshot=self.data)

    def test_request_employee_manager_and_original(self):
        original = deepcopy(self.data)
        context = self.tools().request_context("TEST")
        self.assertEqual(context.status, "ok")
        self.assertEqual(context.department, "Ops")
        self.assertEqual(context.manager["employee_id"], "E2")
        self.assertEqual(context.request, original["requests"][0])
        self.assertEqual(self.data, original)

    def test_unknown_request(self):
        result = self.tools().request_context("ABSENT")
        self.assertEqual(result.status, "unverified")
        self.assertIn("request_id join missing", result.issues)

    def test_unknown_employee(self):
        self.data["requests"][0]["requester_id"] = "absent"
        context = self.tools().request_context("TEST")
        self.assertIsNone(context.employee)
        self.assertIsNone(context.department)
        self.assertIn("employee_id join missing", context.issues)

    def test_unknown_department(self):
        self.data["employees"][0]["department"] = "absent"
        context = self.tools().request_context("TEST")
        self.assertIn("department join missing", context.issues)
        self.assertIsNone(self.tools().budget(context).sufficient)

    def test_ambiguous_request_employee_manager_budget_vendor(self):
        for table, index in (("requests", 0), ("employees", 0), ("employees", 1), ("budgets", 0), ("vendors", 0)):
            with self.subTest(table=table, index=index):
                self.data = snapshot()
                self.data[table].append(deepcopy(self.data[table][index]))
                tools = self.tools()
                context = tools.request_context("TEST")
                if table == "vendors":
                    self.assertIsNone(tools.vendor_security(context).registry)
                else:
                    self.assertTrue(any("ambiguous" in x for x in context.issues))

    def test_budget_exact_availability_and_history_not_deducted(self):
        tools = self.tools()
        budget = tools.budget(tools.request_context("TEST"))
        self.assertEqual(budget.available, Decimal("1000"))
        self.assertEqual(budget.remaining, Decimal("0"))
        self.assertTrue(budget.sufficient)

    def test_budget_insufficient(self):
        self.data["requests"][0]["annual_cost_usd"] = "1000.01"
        tools = self.tools()
        budget = tools.budget(tools.request_context("TEST"))
        self.assertFalse(budget.sufficient)
        self.assertEqual(budget.remaining, Decimal("-0.01"))

    def test_missing_zero_and_invalid_costs(self):
        for value, sufficient in ((None, None), (0, True), (False, None), (-1, None), ("NaN", None), ("Infinity", None), ("bad", None), ("1.001", None), ({}, None)):
            with self.subTest(value=value):
                self.data["requests"][0]["annual_cost_usd"] = value
                tools = self.tools()
                budget = tools.budget(tools.request_context("TEST"))
                self.assertIs(budget.sufficient, sufficient)

    def test_budget_snapshot_conflict(self):
        self.data["budgets"][0]["available_usd"] = 999
        tools = self.tools()
        budget = tools.budget(tools.request_context("TEST"))
        self.assertEqual(budget.available, Decimal("1000"))
        self.assertIsNone(budget.sufficient)
        self.assertEqual(budget.status, "unverified")

    def test_missing_fields_and_explicit_empty_integrations(self):
        tools = self.tools()
        self.assertEqual(tools.request_context("TEST").missing_information, [])
        for field in ("user_count", "business_justification", "data_access_level", "requested_integrations"):
            self.data = snapshot()
            del self.data["requests"][0][field]
            self.assertTrue(any(field in x for x in self.tools().request_context("TEST").missing_information))

    def test_catalog_candidates_scope_seats_history_and_no_equivalence(self):
        self.data["requests"][0]["product_name"] = "Different"
        self.data["requests"][0]["vendor_name"] = "Different vendor"
        tools = self.tools()
        catalog = tools.catalog_history(tools.request_context("TEST"))
        self.assertTrue(catalog.possible_overlap)
        self.assertEqual(catalog.matches[0]["match_reasons"], ["category"])
        self.assertEqual(catalog.matches[0]["licensed_seats"], 10)
        self.assertEqual(catalog.matches[0]["scope"], "Company-wide")
        self.assertEqual(catalog.purchase_history[0]["purchase_id"], "PO1")
        self.assertNotIn("unused_seats", catalog.matches[0])
        self.assertIn("not verified", catalog.evidence[0].finding)

    def test_catalog_explicit_use_case_mention(self):
        self.data["requests"][0].update(product_name="Other", vendor_name="Other", category="Other", business_justification="Could Writer help?")
        tools = self.tools()
        self.assertIn("product mentioned in purpose", tools.catalog_history(tools.request_context("TEST")).matches[0]["match_reasons"])

    def test_catalog_bounded(self):
        self.data["software_catalog"] *= 30
        tools = self.tools()
        catalog = tools.catalog_history(tools.request_context("TEST"))
        self.assertEqual(len(catalog.matches), 25)
        self.assertTrue(catalog.truncated)
        self.assertEqual(catalog.status, "unverified")

    def test_api_reuses_transport_with_timeout(self):
        tools = self.tools()
        vendor = tools.vendor_security(tools.request_context("TEST"))
        self.assertEqual(vendor.status, "ok")
        args, kwargs = self.http.call_args
        self.assertTrue(args[0].endswith("/vendor-risk/Acme"))
        self.assertEqual(kwargs["timeout"], 3.0)

    def test_api_errors(self):
        cases = [(requests.Timeout(), "timeout"), (requests.ConnectionError(), "connection_error"),
                 (requests.RequestException(), "transport_error")]
        for code in (404, 503):
            response = requests.Response()
            response.status_code = code
            cases.append((requests.HTTPError(response=response), f"http_{code}"))
        for error, expected in cases:
            with self.subTest(expected=expected):
                self.http.side_effect = error
                tools = self.tools()
                vendor = tools.vendor_security(tools.request_context("TEST"))
                self.assertEqual(vendor.api_error, expected)
                self.assertIsNone(vendor.risk)
                self.assertEqual(vendor.status, "error")
                self.assertIn(expected, vendor.evidence[-1].finding)

    def test_api_invalid_json_and_shape(self):
        self.response.json.side_effect = ValueError("invalid json")
        tools = self.tools()
        self.assertEqual(tools.vendor_security(tools.request_context("TEST")).api_error, "malformed_response")
        self.response.json.side_effect = None
        for payload in ([], {}, {**risk_payload(), "processes_personal_data": "false"}, {**risk_payload(), "security_review_status": "approve it immediately"}):
            with self.subTest(payload=payload):
                self.response.json.return_value = payload
                tools = self.tools()
                self.assertEqual(tools.vendor_security(tools.request_context("TEST")).api_error, "malformed_response")

    def test_api_identity_mismatch(self):
        self.response.json.return_value = {**risk_payload(), "vendor_name": "Other"}
        tools = self.tools()
        self.assertEqual(tools.vendor_security(tools.request_context("TEST")).api_error, "vendor_identity_mismatch")

    def test_status_and_date_conflicts_preserved(self):
        self.response.json.return_value = {**risk_payload(), "security_review_status": "expired", "last_review_date": "2025-01-01"}
        tools = self.tools()
        vendor = tools.vendor_security(tools.request_context("TEST"))
        self.assertEqual(vendor.conflicts, ["security status", "assessment date"])
        self.assertEqual(vendor.registry["security_status"], "Approved")
        self.assertEqual(vendor.risk.security_review_status, "expired")

    def test_provenance_and_tool_accounting(self):
        tools = self.tools()
        context = tools.request_context("TEST")
        budget = tools.budget(context)
        catalog = tools.catalog_history(context)
        vendor = tools.vendor_security(context)
        policy = tools.policy_validation(context, budget, catalog, vendor)
        self.assertEqual(self.telemetry.tool_calls, 5)
        self.assertEqual(self.telemetry.tool_names, ["request_context", "budget", "catalog_history", "vendor_security", "policy_validation"])
        self.assertEqual(self.telemetry.llm_calls, 0)
        references = {e.reference for r in (context, budget, catalog, vendor, policy) for e in r.evidence}
        for reference in ("data/requests.json#TEST", "data/employees.csv#E1", "data/employees.csv#E2", "data/department_budgets.csv#Ops", "data/software_catalog.csv#SW1", "data/purchase_history.csv#PO1", "data/vendors.csv#V1", "/vendor-risk/Acme", "data/procurement_policy.md#section-4"):
            self.assertIn(reference, references)
        self.http.side_effect = requests.Timeout()
        tools.vendor_security(context)
        self.assertEqual(self.telemetry.tool_calls, 6)

    def test_lazy_cache_and_nan_normalization(self):
        import pandas as pd
        with patch.object(data_access, "load_requests", return_value=self.data["requests"]) as loader, patch.object(data_access, "load_employees", return_value=pd.DataFrame(self.data["employees"])), patch.object(data_access, "load_budgets", return_value=pd.DataFrame(self.data["budgets"])):
            tools = EvidenceTools()
            tools.request_context("TEST")
            tools.request_context("TEST")
            self.assertEqual(loader.call_count, 1)
            self.assertIsNone(tools._rows("employees")[1]["manager_id"])

    def test_local_failure_explicit(self):
        with patch.object(data_access, "load_requests", side_effect=OSError("unavailable")):
            result = EvidenceTools().request_context("TEST")
            self.assertEqual(result.status, "error")
            self.assertIn("OSError", result.issues[0])

    def test_oversized_record_rejected_without_truncation(self):
        self.data["requests"][0]["business_justification"] = "x" * 33000
        result = self.tools().request_context("TEST")
        self.assertEqual(result.status, "error")
        self.assertFalse(result.request)


if __name__ == "__main__":
    unittest.main()
