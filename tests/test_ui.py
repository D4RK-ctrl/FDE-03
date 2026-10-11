"""Offline presentation and headless Streamlit interaction checks."""
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from streamlit.testing.v1 import AppTest

from src.contracts import EvidenceItem, ProcurementDecision, RunTelemetry
from src.ui_presentation import (
    ARCHITECTURES, analysis_label, approval_rows, clear_analysis, cost_label,
    group_evidence, human_review_label, request_overview, risk_explanation,
    run_analysis, sync_selection, telemetry_values,
)

APP = Path(__file__).resolve().parents[1] / "app.py"


def decision(request_id="REQ-1001", fallback=False):
    return ProcurementDecision(
        request_id=request_id, recommendation="Human review required",
        evidence=[EvidenceItem(source="budgets", finding="Original finding", reference="Marketing"),
                  EvidenceItem(source="vendor_risk_api", finding="Assessment unavailable", reference="/vendors/example")],
        required_approvals=["Procurement", "Security"], missing_information=["Assessment date"],
        risk_flags=["security_review_required"] + (["ai_analysis_unavailable", "reviewer_timeout"] if fallback else []),
        next_step="Obtain the required reviews before purchasing.", human_review_required=True,
        telemetry=RunTelemetry(llm_calls=2, tool_calls=5, tool_names=["request_context", "budget"]),
    )


class PresentationTests(unittest.TestCase):
    def test_request_fields_and_join(self):
        request = {"request_id": "R", "requester_id": "E", "product_name": "Tool",
                   "vendor_name": "Vendor", "annual_cost_usd": 0, "user_count": 2,
                   "business_justification": "Need", "data_access_level": "PII",
                   "requested_integrations": [], "urgency": "High"}
        row = request_overview(request, [{"employee_id": "E", "name": "Employee", "department": "Team"}])
        self.assertEqual(row["Requester"], "Employee (E)")
        self.assertEqual(row["Department"], "Team")
        self.assertEqual(row["Annual cost"], "$0.00")
        self.assertEqual(row["Requested integrations"], "None requested")
        self.assertEqual(row["Business justification"], "Need")
        self.assertEqual(row["Data access"], "PII")

    def test_missing_and_ambiguous_records(self):
        row = request_overview({}, [])
        self.assertTrue(all(value == "Missing" for value in row.values()))
        row = request_overview({"requester_id": "E"}, [{"employee_id": "E"}, {"employee_id": "E"}])
        self.assertIn("ambiguous", row["Requester"])
        self.assertEqual(row["Department"], "Missing")

    def test_invalid_and_missing_cost(self):
        for value in (True, "no", "NaN", -1):
            with self.subTest(value=value):
                self.assertEqual(cost_label(value), "Invalid value")
        self.assertEqual(cost_label(None), "Missing")
        self.assertEqual(cost_label("1000.01"), "$1,000.01")

    def test_architecture_and_review_labels(self):
        self.assertEqual(set(ARCHITECTURES), {"single", "staged"})
        self.assertIn("Architecture B", ARCHITECTURES["staged"])
        self.assertIn("pending", human_review_label(True))
        self.assertIn("separate", human_review_label(False))

    def test_approval_names_only_and_pending(self):
        self.assertEqual(approval_rows(decision()), [
            {"Required approval": "Procurement", "Status": "Pending human authorization"},
            {"Required approval": "Security", "Status": "Pending human authorization"}])

    def test_risk_mapping_and_unknown(self):
        self.assertIn("budget", risk_explanation("budget_insufficient"))
        self.assertIn("Reviewer:", risk_explanation("reviewer_timeout"))
        self.assertIn("Additional concern", risk_explanation("new_flag"))

    def test_evidence_grouping_preserves_findings(self):
        result = decision()
        before = result.model_dump()
        groups = group_evidence(result)
        self.assertEqual(groups["Budget"][0]["Finding"], "Original finding")
        self.assertEqual(groups["Vendor and security"][0]["Reference"], "/vendors/example")
        self.assertEqual(before, result.model_dump())

    def test_fallback_and_actual_telemetry(self):
        self.assertIn("accepted", analysis_label(decision()))
        self.assertIn("handoff", analysis_label(decision(fallback=True)))
        self.assertEqual(telemetry_values(decision())["LLM attempts"], "2")
        result = decision()
        result.telemetry = RunTelemetry(llm_calls=0)
        self.assertEqual(telemetry_values(result)["LLM attempts"], "0")
        self.assertEqual(telemetry_values(result)["Tool calls"], "Not reported")

    def test_duplicate_guard_timing_clear_and_selection(self):
        state, handler = {}, Mock(return_value=decision())
        self.assertTrue(run_analysis(state, "REQ-1001", "single", handler, Mock(side_effect=[10, 12])))
        self.assertEqual(state["analysis_latency"], 2)
        self.assertFalse(run_analysis(state, "REQ-1001", "single", handler))
        handler.assert_called_once_with("REQ-1001", architecture="single")
        sync_selection(state, "REQ-1001", "staged")
        self.assertNotIn("analysis_result", state)
        clear_analysis(state)
        self.assertNotIn("analysis_latency", state)

    def test_sanitized_failure_and_invalid_identity(self):
        for handler in (Mock(side_effect=RuntimeError("Authorization: Bearer private-test-key")),
                        Mock(return_value=decision("wrong")), Mock(return_value={})):
            state = {}
            run_analysis(state, "REQ-1001", "single", handler)
            self.assertNotIn("analysis_result", state)
            self.assertNotIn("private-test-key", state["analysis_error"])
            self.assertNotIn("Authorization", state["analysis_error"])
            self.assertFalse(state["analysis_running"])


class HeadlessUITests(unittest.TestCase):
    def setUp(self):
        self.handler = Mock(side_effect=lambda rid, architecture: decision(rid))
        self.patcher = patch("src.solution.handle_request", self.handler)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.app = AppTest.from_file(APP, default_timeout=15).run()
        self.assertEqual(len(self.app.exception), 0)

    def test_selection_never_calls_pipeline(self):
        self.app.selectbox(key="selected_request").set_value("REQ-1002").run()
        self.app.radio(key="selected_architecture").set_value("staged").run()
        self.handler.assert_not_called()
        self.assertEqual(len(self.app.exception), 0)

    def test_result_retention_duplicate_guard_and_clear(self):
        self.app.button(key="analyze").click().run()
        self.assertEqual(len(self.app.exception), 0)
        self.handler.assert_called_once_with("REQ-1001", architecture="single")
        self.assertTrue(self.app.button(key="analyze").disabled)
        self.app.run()
        self.assertEqual(self.handler.call_count, 1)
        self.assertTrue(any(m.value == "5" for m in self.app.metric))
        self.app.button(key="clear").click().run()
        self.assertFalse(self.app.button(key="analyze").disabled)
        self.assertEqual(len(self.app.metric), 0)
        self.assertEqual(self.handler.call_count, 1)

    def test_architecture_change_clears_and_routes(self):
        self.app.button(key="analyze").click().run()
        self.app.radio(key="selected_architecture").set_value("staged").run()
        self.assertEqual(len(self.app.metric), 0)
        self.assertEqual(self.handler.call_count, 1)
        self.app.button(key="analyze").click().run()
        self.handler.assert_called_with("REQ-1001", architecture="staged")

    def test_request_change_clears_stale_result(self):
        self.app.button(key="analyze").click().run()
        self.app.selectbox(key="selected_request").set_value("REQ-1002").run()
        self.assertEqual(len(self.app.metric), 0)
        self.handler.assert_called_once()

    def test_fallback_and_visible_risks(self):
        self.handler.side_effect = lambda rid, architecture: decision(rid, fallback=True)
        self.app.button(key="analyze").click().run()
        self.assertTrue(any("human handoff" in item.value for item in self.app.caption))
        self.assertTrue(any("AI analysis was unavailable" in item.value for item in self.app.warning))
        self.assertTrue(any(item.value == "reviewer_timeout" for item in self.app.text))
        self.assertEqual(len(self.app.exception), 0)

    def test_exception_never_discloses_provider_content(self):
        self.handler.side_effect = RuntimeError("Authorization: private-test-key raw provider body")
        self.app.button(key="analyze").click().run()
        self.assertEqual(len(self.app.exception), 0)
        self.assertEqual(len(self.app.error), 1)
        self.assertNotIn("private-test-key", self.app.error[0].value)
        self.assertIn("Clear results", self.app.error[0].value)
        self.assertEqual(len(self.app.metric), 0)


if __name__ == "__main__":
    unittest.main()
