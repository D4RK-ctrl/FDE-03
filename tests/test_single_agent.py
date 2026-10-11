from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import httpx
import requests

from src.agents import AgentAdvice, single_agent
from src.contracts import ProcurementDecision
from src.llm_client import GroqClient
from src.solution import handle_request
from src.tools import EvidenceTools

ROOT = Path(__file__).resolve().parents[1]


class SingleAgentTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"GROQ_API_KEY": "offline-secret", "MODEL_NAME": "openai/gpt-oss-120b"})
        self.env.start()
        self.addCleanup(self.env.stop)
        # Fixture reads are test-only; production vendor evidence uses HTTP.
        self.risk = json.loads((ROOT / "data/vendor_risk.json").read_text())
        self.vendor_transport = patch("src.vendor_client.requests.get", side_effect=self.vendor_http)
        self.vendor_transport.start()
        self.addCleanup(self.vendor_transport.stop)
        self.requests = []
        self.factory = patch("src.solution.GroqClient", side_effect=lambda counter: GroqClient(counter, transport=httpx.MockTransport(self.model_http)))
        self.factory.start()
        self.addCleanup(self.factory.stop)

    def vendor_http(self, url, **kwargs):
        from urllib.parse import unquote
        name = unquote(url.rsplit("/", 1)[1])
        payload = self.risk.get(name)
        if payload and payload.get("force_error"):
            response = requests.Response()
            response.status_code = 503
            raise requests.HTTPError(response=response)
        response = Mock()
        response.json.return_value = {"vendor_name": name, **payload}
        return response

    def model_http(self, request):
        self.requests.append(request)
        payload = json.loads(request.content)
        envelope = json.loads(payload["messages"][1]["content"])
        req = envelope["untrusted_business_data"]["request_context"]["request"]
        advice = {"request_id": req["request_id"], "recommendation": "proceed_to_human_review",
                  "explanation": "The need and existing alternatives should be assessed by the human reviewers.",
                  "next_step": "Send the evidence package to the required reviewers.",
                  "evidence_references": [envelope["evidence_references"][0]]}
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(advice)}}]})

    def test_success_five_tools_one_model_call(self):
        result = handle_request("REQ-1001")
        self.assertIsInstance(result, ProcurementDecision)
        self.assertNotIn("ai_analysis_unavailable", result.risk_flags)
        self.assertEqual(result.telemetry.llm_calls, 1)
        self.assertEqual(result.telemetry.tool_calls, 5)
        self.assertEqual(result.telemetry.tool_names, ["request_context", "budget", "catalog_history", "vendor_security", "policy_validation"])
        self.assertEqual(result.required_approvals, ["Manager"])

    def test_budget_security_privacy_and_injection_preserved(self):
        result = handle_request("REQ-1005")
        self.assertIn("budget_insufficient", result.risk_flags)
        self.assertTrue({"Finance", "Security", "Privacy", "Legal"}.issubset(result.required_approvals))
        result = handle_request("REQ-1006")
        self.assertIn("prompt_injection_detected", result.risk_flags)
        self.assertIn("missing_information", result.risk_flags)
        self.assertTrue(result.human_review_required)
        self.assertTrue(result.recommendation.startswith("Request clarification"))

    def test_vendor_service_unavailable_still_calls_one_model_and_requires_review(self):
        result = handle_request("REQ-1009")
        self.assertIn("vendor_risk_unavailable", result.risk_flags)
        self.assertIn("Security", result.required_approvals)
        self.assertEqual(result.telemetry.llm_calls, 1)

    def test_unknown_request_no_model_or_vendor_http(self):
        result = handle_request("REQ-UNKNOWN")
        self.assertIn("request_unavailable", result.risk_flags)
        self.assertEqual(result.telemetry.llm_calls, 0)
        self.assertEqual(result.telemetry.tool_calls, 5)
        self.assertFalse(self.requests)

    def test_missing_key_explicit_handoff(self):
        os.environ["GROQ_API_KEY"] = ""
        result = handle_request("REQ-1001")
        self.assertIn("missing_api_key", result.risk_flags)
        self.assertEqual(result.telemetry.llm_calls, 0)
        self.assertEqual(result.required_approvals, ["Manager"])

    def test_provider_failures_handoff_with_attempt_counts(self):
        for status, code, count in ((401, "authentication_error", 1), (429, "rate_limited", 1), (503, "service_unavailable", 2)):
            with self.subTest(status=status), patch("src.llm_client.time.sleep"), patch("src.solution.GroqClient", side_effect=lambda counter: GroqClient(counter, transport=httpx.MockTransport(lambda r: httpx.Response(status, text="offline-secret", headers={"Retry-After": "10"} if status == 429 else {})))):
                result = handle_request("REQ-1005")
                self.assertIn(code, result.risk_flags)
                self.assertEqual(result.telemetry.llm_calls, count)
                self.assertIn("budget_insufficient", result.risk_flags)
                self.assertNotIn("offline-secret", result.model_dump_json())

    def test_provider_output_rejection_retains_policy_and_human_handoff(self):
        with patch("src.solution.GroqClient", side_effect=lambda counter: GroqClient(counter, transport=httpx.MockTransport(
            lambda r: httpx.Response(400, json={"error": {"code": "json_validate_failed",
                                                       "message": "offline-secret private prompt"}})))):
            result = handle_request("REQ-1005")
        self.assertIn("provider_output_validation_failed", result.risk_flags)
        self.assertIn("ai_analysis_unavailable", result.risk_flags)
        self.assertIn("budget_insufficient", result.risk_flags)
        self.assertTrue({"Finance", "Security", "Privacy", "Legal"}.issubset(result.required_approvals))
        self.assertTrue(result.human_review_required)
        self.assertEqual(result.telemetry.llm_calls, 1)
        self.assertEqual(result.telemetry.tool_calls, 5)
        self.assertNotIn("offline-secret", result.model_dump_json())
        self.assertIn("AI advisory did not succeed", result.next_step)

    def test_trusted_prompt_marks_approvals_pending_without_extra_calls(self):
        result = handle_request("REQ-1001")
        prompt = json.loads(self.requests[0].content)["messages"][0]["content"]
        self.assertIn("no purchasing authorization or completed human", prompt)
        self.assertIn("every required approval as pending", prompt)
        self.assertIn("vendor assessments are not purchase approvals", prompt)
        self.assertEqual(result.telemetry.llm_calls, 1)

    def test_unsupported_architecture_explicit(self):
        with self.assertRaisesRegex(ValueError, "Unsupported architecture"):
            handle_request("REQ-1001", architecture="unsupported")
        self.assertFalse(self.requests)

    def test_invalid_request_id(self):
        for request_id in (None, "", "x" * 201):
            with self.assertRaises(ValueError):
                handle_request(request_id)

    def test_no_business_file_mutation(self):
        paths = list((ROOT / "data").glob("*"))
        before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths if p.is_file()}
        handle_request("REQ-1001")
        self.assertEqual(before, {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in before})

    def test_latency_model_and_errors_use_logging_not_public_schema(self):
        with self.assertLogs("src.solution", level="INFO") as captured:
            result = handle_request("REQ-1001")
        self.assertIn("elapsed_ms=", captured.output[0])
        self.assertIn("model=openai/gpt-oss-120b", captured.output[0])
        self.assertIn("error=none", captured.output[0])
        self.assertNotIn("offline-secret", captured.output[0])
        self.assertEqual(set(result.telemetry.model_dump()), {"llm_calls", "tool_calls", "tool_names"})

    def test_prompt_separation_original_inputs_and_no_mutation(self):
        tools = EvidenceTools()
        context = tools.request_context("REQ-1006")
        budget, catalog, vendor = tools.budget(context), tools.catalog_history(context), tools.vendor_security(context)
        policy = tools.policy_validation(context, budget, catalog, vendor)
        before = deepcopy([x.model_dump() for x in (context, budget, catalog, vendor, policy)])
        client = Mock()
        client.complete.return_value = AgentAdvice(request_id="REQ-1006", recommendation="request_clarification", explanation="Clarify missing inputs.", next_step="Ask requester for cost.", evidence_references=["data/requests.json#REQ-1006"])
        single_agent(client, context, budget, catalog, vendor, policy)
        messages = client.complete.call_args.args[0]
        self.assertNotIn(context.request["business_justification"], messages[0]["content"])
        envelope = json.loads(messages[1]["content"])
        self.assertEqual(envelope["untrusted_business_data"]["request_context"]["request"], context.request)
        self.assertEqual(before, [x.model_dump() for x in (context, budget, catalog, vendor, policy)])

    def test_compact_prompt_preserves_all_material_records_policy_and_refs(self):
        tools = EvidenceTools()
        context = tools.request_context("REQ-1005")
        budget, catalog, vendor = tools.budget(context), tools.catalog_history(context), tools.vendor_security(context)
        policy = tools.policy_validation(context, budget, catalog, vendor)
        client = Mock()
        single_agent(client, context, budget, catalog, vendor, policy)
        text = client.complete.call_args.args[0][1]["content"]
        compact = json.loads(text)
        for name, result in (("request_context", context), ("budget", budget), ("catalog_history", catalog), ("vendor_security", vendor)):
            self.assertEqual(compact["untrusted_business_data"][name], result.model_dump(mode="json", exclude={"evidence"}))
        self.assertEqual(compact["application_policy_results"], policy.model_dump(mode="json"))
        refs = {e.reference for result in (context, budget, catalog, vendor, policy) for e in result.evidence if e.reference}
        self.assertEqual(set(compact["evidence_references"]), refs)
        legacy = {"untrusted_business_data": {name: result.model_dump(mode="json") for name, result in (("request_context", context), ("budget", budget), ("catalog_history", catalog), ("vendor_security", vendor))}, "application_policy_results": policy.model_dump(mode="json"), "evidence_references": list(refs)}
        self.assertLess(len(text.encode()), len(json.dumps(legacy).encode()))


if __name__ == "__main__":
    unittest.main()
