import json
import os
import unittest
from unittest.mock import patch

import httpx

from src.agents import AgentAdvice
from src.llm_client import ENDPOINT, GroqClient, LLMError, MAX_INPUT_BYTES, retry_delay
from src.telemetry import RunTelemetryCounter


def advice():
    return {"request_id": "REQ-TEST", "recommendation": "review_required",
            "explanation": "The use case needs human review.", "next_step": "Send the evidence to the manager.",
            "evidence_references": ["data/requests.json#REQ-TEST"]}


def envelope(payload):
    return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(payload)}}]}


class ClientTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"GROQ_API_KEY": "test-secret-never-display", "MODEL_NAME": "openai/gpt-oss-120b"})
        env.start()
        self.addCleanup(env.stop)
        sleep = patch("src.llm_client.time.sleep")
        self.sleep = sleep.start()
        self.addCleanup(sleep.stop)
        self.counter = RunTelemetryCounter()

    def invoke(self, handler, messages=None):
        return GroqClient(self.counter, transport=httpx.MockTransport(handler)).complete(
            messages or [{"role": "user", "content": "Return JSON advice"}], AgentAdvice)

    def assert_error(self, handler, code):
        with self.assertRaises(LLMError) as raised:
            self.invoke(handler)
        self.assertEqual(raised.exception.code, code)
        if os.environ["GROQ_API_KEY"]:
            self.assertNotIn(os.environ["GROQ_API_KEY"], str(raised.exception))

    def test_valid_structured_output_and_documented_payload(self):
        def handler(request):
            self.assertEqual(str(request.url), ENDPOINT)
            self.assertEqual(request.method, "POST")
            payload = json.loads(request.content)
            self.assertEqual(payload["model"], "openai/gpt-oss-120b")
            self.assertEqual(payload["max_completion_tokens"], 1200)
            self.assertEqual(payload["reasoning_effort"], "low")
            self.assertNotIn("tools", payload)
            schema = payload["response_format"]["json_schema"]
            self.assertTrue(schema["strict"])
            self.assertFalse(schema["schema"]["additionalProperties"])
            self.assertEqual(set(schema["schema"]["required"]), set(schema["schema"]["properties"]))
            return httpx.Response(200, json=envelope(advice()))
        self.assertEqual(self.invoke(handler).request_id, "REQ-TEST")
        self.assertEqual(self.counter.llm_calls, 1)

    def test_invalid_outer_and_inner_json_no_retry(self):
        for body in (b"bad json", json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": "bad json"}}]}).encode()):
            self.counter = RunTelemetryCounter()
            self.assert_error(lambda r: httpx.Response(200, content=body), "invalid_json")
            self.assertEqual(self.counter.llm_calls, 1)

    def test_missing_mandatory_fields(self):
        self.assert_error(lambda r: httpx.Response(200, json=envelope({"request_id": "REQ-TEST"})), "invalid_structured_output")

    def test_invalid_response_envelope(self):
        for data in ({}, {"choices": []}, {"choices": [{"finish_reason": "stop", "message": {"content": None}}]}):
            self.assert_error(lambda r: httpx.Response(200, json=data), "invalid_response")

    def test_truncated_refused_or_tool_response(self):
        truncated = envelope(advice())
        truncated["choices"][0]["finish_reason"] = "length"
        self.assert_error(lambda r: httpx.Response(200, json=truncated), "incomplete_response")
        for field in ("refusal", "tool_calls"):
            data = envelope(advice())
            data["choices"][0]["message"][field] = "not permitted"
            self.assert_error(lambda r: httpx.Response(200, json=data), "unsupported_response")

    def test_authentication_errors_no_retry_and_sanitized(self):
        for status in (401, 403):
            self.counter = RunTelemetryCounter()
            self.assert_error(lambda r: httpx.Response(status, text="Authorization test-secret-never-display"), "authentication_error")
            self.assertEqual(self.counter.llm_calls, 1)

    def test_429_retries_once_and_counts_every_attempt(self):
        self.assert_error(lambda r: httpx.Response(429, headers={"Retry-After": "1"}), "rate_limited")
        self.assertEqual(self.counter.llm_calls, 2)
        self.sleep.assert_called_once_with(1.0)

    def test_long_retry_after_no_early_retry(self):
        self.assert_error(lambda r: httpx.Response(429, headers={"Retry-After": "60"}), "rate_limited")
        self.assertEqual(self.counter.llm_calls, 1)
        self.sleep.assert_not_called()

    def test_retry_after_numeric_date_and_invalid(self):
        self.assertEqual(retry_delay("1.5"), 1.5)
        self.assertEqual(retry_delay("Wed, 01 Jan 2020 00:00:00 GMT"), 0)
        self.assertEqual(retry_delay("bad"), .5)
        self.assertGreater(retry_delay("NaN"), 2)

    def test_5xx_retry_then_success(self):
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(503) if len(calls) == 1 else httpx.Response(200, json=envelope(advice()))
        self.invoke(handler)
        self.assertEqual(self.counter.llm_calls, 2)

    def test_5xx_exhausted(self):
        self.assert_error(lambda r: httpx.Response(500), "service_unavailable")
        self.assertEqual(self.counter.llm_calls, 2)

    def test_timeout_connection_errors_bounded(self):
        for error, expected in ((httpx.ReadTimeout, "timeout"), (httpx.ConnectError, "connection_error")):
            self.counter = RunTelemetryCounter()
            def handler(request):
                raise error("test-secret-never-display", request=request)
            self.assert_error(handler, expected)
            self.assertEqual(self.counter.llm_calls, 2)

    def test_other_transport_and_http_errors(self):
        def handler(request):
            raise httpx.RemoteProtocolError("test-secret-never-display", request=request)
        self.assert_error(handler, "transport_error")
        self.assertEqual(self.counter.llm_calls, 1)
        for status, code in ((400, "request_rejected"), (302, "http_error")):
            self.assert_error(lambda r: httpx.Response(status), code)

    def test_missing_key_and_unsupported_model_make_no_call(self):
        os.environ["GROQ_API_KEY"] = ""
        self.assert_error(lambda r: self.fail("HTTP called"), "missing_api_key")
        os.environ["GROQ_API_KEY"] = "test-secret"
        os.environ["MODEL_NAME"] = "other"
        self.assert_error(lambda r: self.fail("HTTP called"), "unsupported_model_configuration")
        self.assertEqual(self.counter.llm_calls, 0)

    def test_input_and_response_bounds(self):
        with self.assertRaises(LLMError) as raised:
            self.invoke(lambda r: self.fail("HTTP called"), [{"role": "user", "content": "x" * MAX_INPUT_BYTES}])
        self.assertEqual(raised.exception.code, "input_too_large")
        self.assertEqual(self.counter.llm_calls, 0)
        self.assert_error(lambda r: httpx.Response(200, content=b"x" * 65000), "response_too_large")

    def test_default_model_and_explicit_timeouts(self):
        os.environ["MODEL_NAME"] = ""
        def handler(request):
            timeouts = request.extensions["timeout"]
            self.assertEqual(timeouts["connect"], 5.0)
            self.assertEqual(timeouts["read"], 15.0)
            self.assertEqual(timeouts["write"], 5.0)
            self.assertEqual(timeouts["pool"], 5.0)
            return httpx.Response(200, json=envelope(advice()))
        self.invoke(handler)

    def test_total_budget_exhausted_before_request(self):
        with patch("src.llm_client.time.monotonic", side_effect=[0.0, 46.0]):
            self.assert_error(lambda r: self.fail("HTTP called"), "timeout")
        self.assertEqual(self.counter.llm_calls, 0)

    def test_429_sanitized_metadata_records_explicit_window_only(self):
        for dimension, phrase in (("tokens_per_minute", "tokens per minute (TPM)"),
                                  ("requests_per_minute", "requests per minute (RPM)"),
                                  ("requests_per_day", "requests per day (RPD)"),
                                  ("tokens_per_day", "tokens per day (TPD)")):
            with self.subTest(dimension=dimension):
                self.counter = RunTelemetryCounter()
                client = GroqClient(self.counter, transport=httpx.MockTransport(lambda r: httpx.Response(
                    429, headers={"Retry-After": "30", "x-ratelimit-limit-requests": "1000", "x-ratelimit-limit-tokens": "8000", "x-ratelimit-remaining-tokens": "0", "Authorization": "test-secret-never-display"},
                    json={"error": {"message": "Rate limit reached on " + phrase + " org secret-test-never-display"}})))
                with self.assertRaises(LLMError):
                    client.complete([{"role": "user", "content": "JSON"}], AgentAdvice)
                record = client.diagnostics[0]
                self.assertEqual(record["rate_limits"]["provider_reported_windows"], [dimension])
                self.assertEqual(record["rate_limits"]["exhausted_header_windows"], ["tokens_per_minute"])
                self.assertEqual(record["rate_limits"]["retry_after_seconds"], 30)
                self.assertEqual(self.counter.llm_calls, 1)
                self.assertNotIn("secret", json.dumps(record))

    def test_429_without_window_evidence_stays_unknown(self):
        client = GroqClient(self.counter, transport=httpx.MockTransport(lambda r: httpx.Response(429, json={"error": {"message": "Rate limit exceeded"}}, headers={"Retry-After": "10", "x-ratelimit-remaining-tokens": "credential-text"})))
        with self.assertRaises(LLMError):
            client.complete([{"role": "user", "content": "JSON"}], AgentAdvice)
        limits = client.diagnostics[0]["rate_limits"]
        self.assertEqual(limits["provider_reported_windows"], [])
        self.assertEqual(limits["exhausted_header_windows"], [])
        self.assertNotIn("credential-text", json.dumps(limits))

    def test_timeout_during_429_diagnostics_does_not_hide_rate_limit(self):
        class FailingStream(httpx.SyncByteStream):
            def __iter__(self):
                raise httpx.ReadTimeout("secret transport detail")
                yield b""
        self.assert_error(lambda r: httpx.Response(429, headers={"Retry-After": "10"}, stream=FailingStream()), "rate_limited")
        self.assertEqual(self.counter.llm_calls, 1)

    def test_retry_deadline_exhaustion_prevents_second_attempt(self):
        with patch("src.llm_client.time.monotonic", side_effect=[0.0, 0.0, 46.0]):
            self.assert_error(lambda r: httpx.Response(503), "service_unavailable")
        self.assertEqual(self.counter.llm_calls, 1)
        self.sleep.assert_not_called()

    def test_400_known_codes_classified_without_retry_or_sensitive_diagnostics(self):
        for code, expected in (("json_validate_failed", "provider_output_validation_failed"),
                               ("context_length_exceeded", "context_limit_exceeded"),
                               ("invalid_json_schema", "provider_schema_rejected"),
                               ("unsupported_parameter", "unsupported_parameter")):
            with self.subTest(code=code):
                self.counter = RunTelemetryCounter()
                client = GroqClient(self.counter, transport=httpx.MockTransport(lambda r: httpx.Response(
                    400, json={"error": {"code": code, "type": "invalid_request_error", "param": "response_format",
                                          "message": "test-secret-never-display private business prompt",
                                          "failed_generation": "test-secret-never-display"}},
                    headers={"Authorization": "test-secret-never-display"})))
                with self.assertLogs("src.llm_client", level="INFO") as logs:
                    with self.assertRaises(LLMError) as raised:
                        client.complete([{"role": "user", "content": "sensitive request"}], AgentAdvice)
                self.assertEqual(raised.exception.code, expected)
                diagnostic = client.diagnostics[0]["provider_error"]
                self.assertEqual(diagnostic["provider_code"], code)
                self.assertEqual(diagnostic["provider_param"], "response_format")
                self.assertEqual(diagnostic["classification"], expected)
                self.assertEqual(self.counter.llm_calls, 1)
                self.sleep.assert_not_called()
                for output in (json.dumps(client.diagnostics), str(raised.exception), str(logs.output)):
                    self.assertNotIn("test-secret-never-display", output)
                    self.assertNotIn("private business prompt", output)
                    self.assertNotIn("sensitive request", output)

    def test_400_unknown_malformed_or_oversized_details_stay_unknown(self):
        bodies = (b"invalid json", b"[]", b'{"error":null}', b"x" * 4097,
                  json.dumps({"error": {"code": "test-secret-never-display", "type": ["bad"],
                                        "param": "business-secret", "message": "context limit unsupported schema"}}).encode())
        for body in bodies:
            with self.subTest(size=len(body)):
                self.counter = RunTelemetryCounter()
                client = GroqClient(self.counter, transport=httpx.MockTransport(lambda r: httpx.Response(400, content=body)))
                with self.assertRaises(LLMError) as raised:
                    client.complete([{"role": "user", "content": "JSON"}], AgentAdvice)
                self.assertEqual(raised.exception.code, "request_rejected")
                self.assertEqual(self.counter.llm_calls, 1)
                self.assertNotIn("provider_code", client.diagnostics[0]["provider_error"])
                self.assertNotIn("secret", json.dumps(client.diagnostics))

    def test_timeout_during_400_body_retains_original_http_error(self):
        class FailingStream(httpx.SyncByteStream):
            def __iter__(self):
                raise httpx.ReadTimeout("secret transport detail")
                yield b""
        self.assert_error(lambda r: httpx.Response(400, stream=FailingStream()), "request_rejected")
        self.assertEqual(self.counter.llm_calls, 1)

    def test_local_schema_bounds_remain_enforced(self):
        for change in ({"explanation": "x" * 1201}, {"evidence_references": []},
                       {"recommendation": "approved"}, {"unexpected": True}):
            with self.subTest(change=list(change)):
                self.counter = RunTelemetryCounter()
                self.assert_error(lambda r: httpx.Response(200, json=envelope({**advice(), **change})),
                                  "invalid_structured_output")
                self.assertEqual(self.counter.llm_calls, 1)
                self.sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
