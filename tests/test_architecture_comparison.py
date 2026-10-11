"""Synthetic offline comparisons through the real application pipeline."""
import json
import logging
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.parse import unquote

import httpx
import requests

from evals.compare_architectures import (
    AttemptControl, ComparisonPacer, compare, derived_checks, markdown_report, select_cases, write_reports,
)
from evals.run_public_evals import evaluate
from src.contracts import ProcurementDecision

ROOT = Path(__file__).resolve().parents[1]


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"GROQ_API_KEY": "offline-comparison-secret", "MODEL_NAME": "openai/gpt-oss-120b"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.risks = json.loads((ROOT / "data/vendor_risk.json").read_text())
        self.vendor = patch("src.vendor_client.requests.get", side_effect=self.vendor_http)
        self.vendor.start()
        self.addCleanup(self.vendor.stop)
        self.responses = []
        self.sent = []

    def vendor_http(self, url, **kwargs):
        name = unquote(url.rsplit("/", 1)[1])
        payload = self.risks[name]
        response = Mock()
        if payload.get("force_error"):
            response = requests.Response()
            response.status_code = 503
            raise requests.HTTPError(response=response)
        response.json.return_value = {"vendor_name": name, **payload}
        return response

    def model_http(self, request):
        self.sent.append(request)
        if self.responses:
            response = self.responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response
        payload = json.loads(request.content)
        package = json.loads(payload["messages"][1]["content"])
        advice = {
            "request_id": package["untrusted_business_data"]["request_context"]["request"]["request_id"],
            "recommendation": "review_required", "explanation": "Review suitability and unresolved evidence with humans.",
            "next_step": "Request the required approvals before proceeding.",
            "evidence_references": [package["evidence_references"][0]],
        }
        if "concerns" in payload["response_format"]["json_schema"]["schema"]["properties"]:
            advice["concerns"] = []
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(advice)}}]})

    def run_comparison(self, cases=None, architectures=("single", "staged"), **kwargs):
        return compare(cases or select_cases(), architectures,
                       control=kwargs.pop("control", AttemptControl(18, 0, 0)),
                       execution_mode="synthetic",
                       transport_factory=lambda: httpx.MockTransport(self.model_http), **kwargs)

    def test_six_cases_both_routes_actual_counts_and_public_scoring(self):
        with patch("evals.compare_architectures.evaluate", wraps=evaluate) as scoring:
            result = self.run_comparison()
        self.assertEqual(scoring.call_count, 12)
        self.assertTrue(result["complete"])
        self.assertEqual(result["llm_attempts"], 18)
        self.assertEqual(len(self.sent), 18)
        self.assertEqual([r["case_id"] for r in result["rows"] if r["architecture"] == "single"],
                         [r["case_id"] for r in result["rows"] if r["architecture"] == "staged"])
        for row in result["rows"]:
            self.assertTrue(row["schema_valid"])
            self.assertTrue(row["minimum_checks_passed"])
            self.assertTrue(row["ai_accepted"])
            self.assertEqual(row["tool_calls"], 5)
            self.assertEqual(len(row["tool_names"]), 5)
            self.assertTrue(all(v is not False for v in row["checks"].values()))
            self.assertEqual(row["checks"]["prompt_injection_resistance"], "human_review_required")
        self.assertEqual(result["summary"]["staged"]["llm_attempts"], 12)
        self.assertEqual(result["summary"]["single"]["tool_calls"], 30)
        self.assertTrue(all(result["paired_evidence_snapshot_matches"].values()))
        original_requests = {r["request_id"]: r for r in json.loads((ROOT / "data/requests.json").read_text())}
        for request in self.sent:
            payload = json.loads(request.content)
            envelope = json.loads(payload["messages"][1]["content"])
            original = envelope["untrusted_business_data"]["request_context"]["request"]
            self.assertEqual(original, original_requests[original["request_id"]])

    def test_subset_architecture_and_unknown_selection(self):
        cases = select_cases(["PUB-02", "PUB-01"])
        self.assertEqual([c["case_id"] for c in cases], ["PUB-01", "PUB-02"])
        result = self.run_comparison(cases, ("staged",))
        self.assertEqual(len(result["rows"]), 2)
        self.assertEqual(result["llm_attempts"], 4)
        with self.assertRaises(ValueError):
            select_cases(["private-case"])
        with self.assertRaises(ValueError):
            self.run_comparison(architectures=("other",))

    def test_fallback_is_not_accepted_and_retries_disabled(self):
        self.responses = [httpx.Response(503)]
        result = self.run_comparison(select_cases(["PUB-01"]), ("single",))
        row = result["rows"][0]
        self.assertTrue(row["minimum_checks_passed"])
        self.assertTrue(row["fallback"])
        self.assertFalse(row["ai_accepted"])
        self.assertEqual(row["fallback_reason"], "service_unavailable")
        self.assertEqual(result["llm_attempts"], 1)
        self.assertTrue(row["checks"]["safe_provider_failure"])

    def test_429_daily_quota_stops_and_metadata_sanitized(self):
        self.responses = [httpx.Response(429, json={"error": {"message": "tokens per day offline-comparison-secret"}},
                                       headers={"x-ratelimit-remaining-requests": "0", "authorization": "secret"})]
        result = self.run_comparison()
        self.assertEqual(result["executed_runs"], 1)
        self.assertEqual(result["stop_reason"], "evaluation_daily_quota")
        self.assertEqual(result["llm_attempts"], 1)
        self.assertNotIn("offline-comparison-secret", json.dumps(result))
        self.assertNotIn("authorization", json.dumps(result["rows"][0]["diagnostics"]).lower())

    def test_timeout_counted_once(self):
        self.responses = [httpx.ReadTimeout("offline-comparison-secret")]
        result = self.run_comparison(select_cases(["PUB-01"]), ("single",))
        self.assertEqual(result["llm_attempts"], 1)
        self.assertEqual(result["rows"][0]["fallback_reason"], "timeout")
        self.assertNotIn("offline-comparison-secret", json.dumps(result))

    def test_response_stream_failure_cannot_retry_or_exceed_budget(self):
        class BrokenStream(httpx.SyncByteStream):
            def __iter__(self):
                yield b'{"choices":'
                raise httpx.ReadError("offline-comparison-secret")
        self.responses = [httpx.Response(200, stream=BrokenStream())]
        result = self.run_comparison(select_cases(["PUB-01"]), ("single",), control=AttemptControl(1, 0, 0))
        self.assertEqual(result["llm_attempts"], 1)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(result["rows"][0]["fallback_reason"], "connection_error")
        self.assertNotIn("offline-comparison-secret", json.dumps(result))

    def test_budget_can_stop_between_analyst_and_reviewer(self):
        result = self.run_comparison(select_cases(["PUB-01", "PUB-02"]), ("staged",), control=AttemptControl(1, 0, 0))
        self.assertEqual(result["llm_attempts"], 1)
        self.assertFalse(result["complete"])
        self.assertEqual(result["executed_runs"], 1)
        row = result["rows"][0]
        self.assertEqual(row["fallback_reason"], "reviewer_evaluation_attempt_budget")
        self.assertTrue(row["checks"]["telemetry_consistent"])

    def test_pacing_measured_and_active_latency_separate(self):
        control = AttemptControl(3, 2, 5)
        virtual = [0.0]
        def sleep(seconds):
            virtual[0] += seconds
        control.clock = lambda: virtual[0]
        with patch("scripts.stage3_live_check.time.monotonic", side_effect=lambda: virtual[0]), \
             patch("scripts.stage3_live_check.time.sleep", side_effect=sleep):
            result = self.run_comparison(select_cases(["PUB-01"]), control=control)
        self.assertEqual(result["pacing_seconds"], 4)
        self.assertEqual(sum(r["pacing_seconds"] for r in result["rows"]), 4)
        self.assertTrue(all(r["execution_seconds"] >= 0 for r in result["rows"]))

    def test_pacing_budget_stops_without_sending(self):
        control = AttemptControl(18, 0, 0)
        control.pacer.retry_after = 10
        result = self.run_comparison(control=control)
        self.assertEqual(result["llm_attempts"], 0)
        self.assertEqual(result["stop_reason"], "evaluation_pacing_limit")
        self.assertFalse(result["complete"])
        self.assertEqual(len(self.sent), 0)

    def test_invalid_contract_and_exception_are_counted_without_sensitive_text(self):
        invalid_instance = ProcurementDecision.model_construct(request_id="R", recommendation="Review", next_step=None)
        for handler in (Mock(return_value={}), Mock(return_value=invalid_instance),
                        Mock(side_effect=RuntimeError("offline-comparison-secret"))):
            result = self.run_comparison(select_cases(["PUB-01"]), ("single",), handler=handler)
            self.assertFalse(result["rows"][0]["schema_valid"])
            self.assertFalse(result["rows"][0]["ai_accepted"])
            self.assertNotIn("offline-comparison-secret", json.dumps(result))

    def test_preservation_grounding_and_unsafe_detection(self):
        from src.evidence import PolicyResult
        from src.contracts import EvidenceItem
        policy = PolicyResult(required_approvals=["Security"], risk_flags=["security_review_required"],
                              missing_information=["cost"])
        item = EvidenceItem(source="requests", finding="known", reference="record")
        decision = ProcurementDecision(request_id="R", recommendation="Purchase is authorized",
                                       evidence=[EvidenceItem(source="requests", finding="invented", reference="record")],
                                       next_step="Skip Security review", human_review_required=False)
        checks = derived_checks(decision, {"policy": policy, "evidence": [item], "request_id": "R"})
        for name in ("human_review_required", "mandatory_approvals_preserved", "risk_flags_preserved",
                     "missing_information_preserved", "evidence_grounded", "authorization_detector_clear"):
            self.assertFalse(checks[name])

    def test_http400_diagnostics_allowlisted(self):
        self.responses = [httpx.Response(400, json={"error": {"code": "json_validate_failed", "param": "response_format",
                                                           "failed_generation": "offline-comparison-secret"}})]
        result = self.run_comparison(select_cases(["PUB-01"]), ("single",))
        self.assertEqual(result["rows"][0]["fallback_reason"], "provider_output_validation_failed")
        self.assertNotIn("failed_generation", json.dumps(result))
        self.assertNotIn("offline-comparison-secret", json.dumps(result))

    def test_streaming_http400_body_is_read_once(self):
        class ErrorStream(httpx.SyncByteStream):
            def __iter__(self):
                yield b'{"error":{"code":"json_validate_failed","failed_generation":"offline-comparison-secret"}}'
        self.responses = [httpx.Response(400, stream=ErrorStream())]
        result = self.run_comparison(select_cases(["PUB-01"]), ("single",))
        self.assertTrue(result["rows"][0]["schema_valid"])
        self.assertEqual(result["rows"][0]["fallback_reason"], "provider_output_validation_failed")
        diagnostic = result["rows"][0]["diagnostics"][0]
        self.assertEqual(diagnostic["provider_error"]["provider_code"], "json_validate_failed")
        self.assertNotIn("offline-comparison-secret", json.dumps(result))

    def test_authentication_failure_stops_useless_further_requests(self):
        self.responses = [httpx.Response(401)]
        result = self.run_comparison()
        self.assertEqual(result["llm_attempts"], 1)
        self.assertEqual(result["executed_runs"], 1)
        self.assertEqual(result["stop_reason"], "authentication_error")

    def test_missing_key_is_zero_attempt_handoff(self):
        with patch.dict(os.environ, {"GROQ_API_KEY": ""}):
            result = self.run_comparison()
        self.assertEqual(result["llm_attempts"], 0)
        self.assertEqual(result["rows"][0]["fallback_reason"], "missing_api_key")
        self.assertEqual(len(self.sent), 0)

    def test_reports_and_historical_protection(self):
        result = self.run_comparison(select_cases(["PUB-01"]), ("single",))
        with tempfile.TemporaryDirectory() as directory:
            old = Path(directory) / "STAGE_3_EVALUATE_RESULTS.json"
            old.write_text("historical")
            first = write_reports(result, directory)
            second = write_reports(result, directory)
            self.assertNotEqual(first, second)
            self.assertEqual(old.read_text(), "historical")
            self.assertEqual(json.loads(first[0].read_text())["llm_attempts"], 1)
            self.assertIn("No architecture winner", first[1].read_text())
            with patch("evals.compare_architectures.datetime") as now:
                now.now.return_value.strftime.return_value = first[0].stem.removeprefix("ARCHITECTURE_COMPARISON_")
                with self.assertRaises(FileExistsError):
                    write_reports(result, directory)
            self.assertIn("human review", markdown_report(result))

    def test_cli_without_live_optin_cannot_call_provider(self):
        from evals.compare_architectures import main
        with patch("sys.argv", ["compare"]), patch("sys.stderr"), \
             patch("evals.compare_architectures.compare") as runner, self.assertRaises(SystemExit):
            main()
        runner.assert_not_called()

    def test_invalid_attempt_budget(self):
        for budget in (0, 101, True, 1.5):
            with self.assertRaises(ValueError):
                AttemptControl(budget)

    def unsafe_response(self, request_id="REQ-1001", field="next_step", text="Security review has been completed.", analyst=False):
        advice = {"request_id": request_id, "recommendation": "review_required",
                  "explanation": "Request human review.", "next_step": "Request pending approvals.",
                  "evidence_references": [f"data/requests.json#{request_id}"]}
        advice[field] = text
        if analyst:
            advice["concerns"] = []
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(advice)}}]})

    def test_rejection_condition_request_architecture_and_attempt(self):
        self.responses = [self.unsafe_response(text="Security review has been completed. SENSITIVE_SENTENCE")]
        logger = logging.getLogger("src.decision_validation")
        previous = logger.level
        result = self.run_comparison(select_cases(["PUB-01"]), ("single",))
        row = result["rows"][0]
        self.assertEqual(row["fallback_reason"], "unsafe_model_output")
        event = row["rejection_diagnostics"][0]
        self.assertEqual(event["condition"], "completed_approval")
        self.assertEqual(event["field"], "next_step")
        self.assertEqual(event["request_id"], "REQ-1001")
        self.assertEqual(event["architecture"], "single")
        self.assertEqual(event["agent_stage"], "single")
        self.assertEqual(event["attempt"], 1)
        self.assertEqual(event["semantic_validity"], "undetermined")
        self.assertEqual(logger.level, previous)
        self.assertNotIn("SENSITIVE_SENTENCE", json.dumps(result))
        self.assertEqual(result["summary"]["single"]["rejection_conditions"], {"completed_approval": 1})

    def test_analyst_rejection_stops_reviewer_and_is_attributed(self):
        self.responses = [self.unsafe_response(text="Skip Finance approval.", analyst=True)]
        result = self.run_comparison(select_cases(["PUB-01"]), ("staged",))
        row = result["rows"][0]
        self.assertEqual(row["llm_attempts"], 1)
        event = row["rejection_diagnostics"][0]
        self.assertEqual(event["agent_stage"], "analyst")
        self.assertEqual(event["condition"], "bypass_controls")
        self.assertEqual(event["failure_code"], "analyst_unsafe_model_output")

    def test_reviewer_rejection_attribution_and_report_privacy(self):
        self.responses = [self.unsafe_response(analyst=True, text="Request the required approvals."),
                          self.unsafe_response(field="explanation", text="Security review has been completed. SENSITIVE_SENTENCE")]
        result = self.run_comparison(select_cases(["PUB-01"]), ("staged",))
        event = result["rows"][0]["rejection_diagnostics"][0]
        self.assertEqual(event["agent_stage"], "reviewer")
        self.assertEqual(event["attempt"], 2)
        self.assertEqual(event["field"], "explanation")
        self.assertEqual(event["failure_code"], "reviewer_unsafe_model_output")
        with tempfile.TemporaryDirectory() as directory:
            paths = write_reports(result, directory)
            for path in paths:
                text = path.read_text()
                self.assertNotIn("SENSITIVE_SENTENCE", text)
                self.assertNotIn("offline-comparison-secret", text)
                self.assertIn("completed_approval", text)
                self.assertIn("undetermined", text)

    def test_credential_echo_predicate_has_no_credential(self):
        self.responses = [self.unsafe_response(field="explanation", text="offline-comparison-secret")]
        result = self.run_comparison(select_cases(["PUB-01"]), ("single",))
        self.assertEqual(result["rows"][0]["rejection_diagnostics"][0]["condition"], "credential_echo")
        self.assertNotIn("offline-comparison-secret", json.dumps(result))

    def test_unassociated_and_unallowlisted_diagnostics_do_not_guess(self):
        from src.decision_validation import record_rejection
        def handler(rid, architecture):
            record_rejection("SENSITIVE_CONDITION", "offline-comparison-secret")
            return {}
        result = self.run_comparison(select_cases(["PUB-01"]), ("single",), handler=handler)
        event = result["rows"][0]["rejection_diagnostics"][0]
        self.assertIsNone(event["attempt"])
        self.assertIsNone(event["agent_stage"])
        self.assertEqual(event["attempt_association"], "unavailable")
        self.assertEqual(event["condition"], "unclassified_condition")
        self.assertEqual(event["field"], "unclassified_field")
        self.assertNotIn("SENSITIVE_CONDITION", json.dumps(result))
        self.assertNotIn("offline-comparison-secret", json.dumps(result))

    def test_full_18_attempt_1020_second_pacing_allowance(self):
        virtual = [0.0]
        control = AttemptControl(18, 60, 1020, clock=lambda: virtual[0])
        def sleep(seconds):
            virtual[0] += seconds
        with patch("scripts.stage3_live_check.time.monotonic", side_effect=lambda: virtual[0]), \
             patch("scripts.stage3_live_check.time.sleep", side_effect=sleep), \
             patch("evals.compare_architectures.time.perf_counter", side_effect=lambda: virtual[0]), \
             patch("evals.compare_architectures.evaluate", wraps=evaluate) as scoring:
            result = self.run_comparison(control=control)
        self.assertTrue(result["complete"])
        self.assertEqual(result["llm_attempts"], 18)
        self.assertEqual(result["pacing_seconds"], 1020)
        self.assertEqual(result["wall_seconds"], 1020)
        self.assertEqual(sum(row["tool_calls"] for row in result["rows"]), 60)
        self.assertEqual(scoring.call_count, 12)
        self.assertEqual(result["configuration"]["feasibility"]["nominal_pacing_seconds"], 1020)

    def test_insufficient_full_scope_budget_rejected_before_tools_or_http(self):
        with self.assertRaisesRegex(ValueError, "need 1020 seconds"):
            self.run_comparison(control=AttemptControl(18, 60, 300))
        self.assertEqual(len(self.sent), 0)
        from evals.compare_architectures import main
        with patch("sys.argv", ["compare", "--live", "--interval-seconds", "60", "--max-pacing-seconds", "300"]), \
             patch("sys.stderr"), patch("evals.compare_architectures.compare") as runner, self.assertRaises(SystemExit):
            main()
        runner.assert_not_called()

    def test_partial_budget_feasibility_and_early_failure_reduce_actual_waits(self):
        control = AttemptControl(1, 60, 0)
        result = self.run_comparison(control=control)
        self.assertEqual(result["llm_attempts"], 1)
        self.assertFalse(result["complete"])
        self.assertEqual(result["configuration"]["feasibility"]["budgeted_attempts"], 1)
        self.responses = [httpx.Response(401)]
        result = self.run_comparison(control=AttemptControl(18, 60, 1020))
        self.assertEqual(result["llm_attempts"], 1)
        self.assertLess(result["pacing_seconds"], 1)
        self.assertEqual(result["stop_reason"], "authentication_error")

    def test_invalid_comparison_pacing_caps(self):
        for interval, maximum in ((60, -1), (60, 3601), (60, float("nan")), (61, 1020)):
            with self.assertRaises(ValueError):
                ComparisonPacer(interval, maximum)

    def test_multiple_run_diagnostics_are_independent(self):
        self.responses = [self.unsafe_response(), self.unsafe_response(analyst=True, text="Skip Finance approval.")]
        result = self.run_comparison(select_cases(["PUB-01"]))
        events = [row["rejection_diagnostics"][0] for row in result["rows"]]
        self.assertEqual([e["architecture"] for e in events], ["single", "staged"])
        self.assertEqual([e["attempt"] for e in events], [1, 2])
        self.assertEqual([e["agent_stage"] for e in events], ["single", "analyst"])
