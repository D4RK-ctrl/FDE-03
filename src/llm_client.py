"""Bounded Groq chat-completion transport; no SDK or hidden retries."""
from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import math
import os
import time
import logging
import re
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from src.telemetry import RunTelemetryCounter

MODEL_NAME = "openai/gpt-oss-120b"
ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"
MAX_INPUT_BYTES = 48000
MAX_RESPONSE_BYTES = 64000
MAX_COMPLETION_TOKENS = 1200
MAX_BACKOFF_SECONDS = 2.0
TOTAL_BUDGET_SECONDS = 45.0
TIMEOUT = httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=5.0)
T = TypeVar("T", bound=BaseModel)
logger = logging.getLogger(__name__)
MAX_ERROR_BYTES = 4096
# Exact allowlists: never log arbitrary provider codes, params, messages or
# failed_generation (which can contain the prompt, business data or secrets).
PROVIDER_ERROR_CODES = {
    "json_validate_failed": "provider_output_validation_failed",
    "context_length_exceeded": "context_limit_exceeded",
    "invalid_json_schema": "provider_schema_rejected",
    "unsupported_parameter": "unsupported_parameter",
    "invalid_request_error": "request_rejected",
}
PROVIDER_ERROR_TYPES = {"invalid_request_error", "invalid_request", "validation_error"}
PROVIDER_ERROR_PARAMS = {"response_format", "response_format.json_schema", "messages", "model",
                         "max_completion_tokens", "reasoning_effort", "stream"}


def bad_request_metadata(response: httpx.Response, deadline: float) -> dict:
    """Bounded optional troubleshooting; failed parsing retains HTTP 400.

    A known code is provider-reported evidence, not proof of a local defect.
    Unknown metadata stays unknown rather than classifying by sensitive prose.
    """
    diagnostic = {"classification": "request_rejected", "body_state": "unavailable"}
    content = bytearray()
    try:
        for chunk in response.iter_bytes():
            if len(content) + len(chunk) > MAX_ERROR_BYTES:
                diagnostic["body_state"] = "too_large"
                return diagnostic
            if time.monotonic() >= deadline:
                return diagnostic
            content.extend(chunk)
        parsed = json.loads(content)
    except (ValueError, UnicodeDecodeError, httpx.TransportError):
        return diagnostic
    error = parsed.get("error") if isinstance(parsed, dict) else None
    if not isinstance(error, dict):
        diagnostic["body_state"] = "invalid_envelope"
        return diagnostic
    diagnostic["body_state"] = "parsed"
    for field, allowed in (("code", PROVIDER_ERROR_CODES), ("type", PROVIDER_ERROR_TYPES),
                           ("param", PROVIDER_ERROR_PARAMS)):
        value = error.get(field)
        if isinstance(value, str) and value in allowed:
            diagnostic["provider_" + field] = value
    diagnostic["classification"] = PROVIDER_ERROR_CODES.get(diagnostic.get("provider_code"), "request_rejected")
    return diagnostic


def rate_metadata(response: httpx.Response, deadline: float | None = None) -> dict:
    """Only validated numeric rate headers and known constraint names survive.

Groq documents request headers as RPD and token headers as TPM. Neither proves
RPM/TPD exhaustion. Preserve evidence, not an unsupported cause inference.
"""
    metadata = {}
    for header in ("limit-requests", "remaining-requests", "limit-tokens", "remaining-tokens"):
        value = response.headers.get("x-ratelimit-" + header, "")
        if re.fullmatch(r"\d{1,12}", value):
            metadata[header.replace("-", "_")] = int(value)
    for kind in ("requests", "tokens"):
        value = response.headers.get("x-ratelimit-reset-" + kind, "")
        if re.fullmatch(r"(?:\d{1,6}(?:\.\d{1,3})?(?:ms|s|m|h|d)){1,5}", value):
            metadata["reset_" + kind] = value
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        seconds = retry_delay(retry_after)
        if seconds <= 86400:
            metadata["retry_after_seconds"] = seconds
    metadata["exhausted_header_windows"] = [
        window for field, window in (("remaining_requests", "requests_per_day"), ("remaining_tokens", "tokens_per_minute"))
        if metadata.get(field) == 0]
    if response.status_code == 429:
        body = bytearray()
        try:
            for chunk in response.iter_bytes():
                if len(body) + len(chunk) > 4096 or (deadline is not None and time.monotonic() >= deadline):
                    body.clear()
                    break
                body.extend(chunk)
            parsed = json.loads(body) if body else {}
            error = parsed.get("error", {}) if isinstance(parsed, dict) else {}
            message = error.get("message", "") if isinstance(error, dict) else ""
            if isinstance(message, str):
                windows = {
                    "requests_per_minute": r"requests per minute|\bRPM\b",
                    "requests_per_day": r"requests per day|\bRPD\b",
                    "tokens_per_minute": r"(?<!input )(?<!output )tokens per minute|\bTPM\b",
                    "tokens_per_day": r"tokens per day|\bTPD\b",
                    "input_tokens_per_minute": r"input tokens per minute|\bITPM\b",
                    "output_tokens_per_minute": r"output tokens per minute|\bOTPM\b",
                }
                metadata["provider_reported_windows"] = [name for name, pattern in windows.items() if re.search(pattern, message, re.I)]
        except (ValueError, httpx.TransportError):
            pass  # Optional diagnostics cannot replace the original HTTP 429.
    return metadata


class LLMError(Exception):
    """Only controlled codes are exposed, never HTTP bodies or headers."""
    def __init__(self, code: str):
        self.code = code
        super().__init__(f"Groq advisory unavailable: {code}")


def strict_schema(model: type[BaseModel]) -> dict:
    schema = model.model_json_schema()

    def adapt(node):
        if isinstance(node, dict):
            # Local validation enforces bounds; keep provider schema to its subset.
            for key in ("minLength", "maxLength", "minItems", "maxItems", "default"):
                node.pop(key, None)
            if node.get("type") == "object":
                node["additionalProperties"] = False
                node["required"] = list(node.get("properties", {}))
            for value in node.values():
                adapt(value)
        elif isinstance(node, list):
            for value in node:
                adapt(value)

    adapt(schema)
    return schema


def retry_delay(header: str | None) -> float:
    if not header:
        return 0.5
    try:
        seconds = float(header)
    except ValueError:
        try:
            target = parsedate_to_datetime(header)
            if target.tzinfo is None:
                target = target.replace(tzinfo=timezone.utc)
            seconds = (target - datetime.now(timezone.utc)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return 0.5
    return max(0.0, seconds) if math.isfinite(seconds) else MAX_BACKOFF_SECONDS + 1


class GroqClient:
    def __init__(self, telemetry: RunTelemetryCounter, *, transport: httpx.BaseTransport | None = None):
        self.telemetry = telemetry
        self.transport = transport
        self.model = os.getenv("MODEL_NAME") or MODEL_NAME
        self.diagnostics: list[dict] = []

    def _record(self, diagnostic: dict) -> None:
        self.diagnostics.append(diagnostic)
        logger.info("groq_attempt_metadata %s", json.dumps(diagnostic, separators=(",", ":")),
                    extra={"groq_diagnostic": diagnostic})

    def complete(self, messages: list[dict[str, str]], response_model: type[T]) -> T:
        key = os.getenv("GROQ_API_KEY", "").strip()
        if not key:
            raise LLMError("missing_api_key")
        if self.model != MODEL_NAME:
            raise LLMError("unsupported_model_configuration")
        payload = {"model": self.model, "messages": messages,
                   "max_completion_tokens": MAX_COMPLETION_TOKENS,
                   "reasoning_effort": "low", "stream": False,
                   "response_format": {"type": "json_schema", "json_schema": {
                       "name": "procurement_advice", "strict": True,
                       "schema": strict_schema(response_model)}}}
        input_bytes = len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        if input_bytes > MAX_INPUT_BYTES:
            raise LLMError("input_too_large")
        deadline = time.monotonic() + TOTAL_BUDGET_SECONDS
        # httpx has no automatic retries; redirects are disabled to protect auth.
        with httpx.Client(timeout=TIMEOUT, transport=self.transport, follow_redirects=False) as client:
            for attempt in range(2):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise LLMError("timeout")
                delay = 0.5
                retryable = False
                error = "transport_error"
                try:
                    self.telemetry.record_llm_call()
                    with client.stream("POST", ENDPOINT, headers={"Authorization": f"Bearer {key}"},
                                       json=payload, timeout=httpx.Timeout(
                                           connect=min(5.0, remaining), read=min(15.0, remaining),
                                           write=min(5.0, remaining), pool=min(5.0, remaining))) as response:
                        status = response.status_code
                        diagnostic = {"attempt": attempt + 1, "http_status": status,
                                      "input_bytes": input_bytes, "completion_tokens_limit": MAX_COMPLETION_TOKENS,
                                      "rate_limits": rate_metadata(response, deadline)}
                        if status == 400:
                            diagnostic["provider_error"] = bad_request_metadata(response, deadline)
                        self._record(diagnostic)
                        if status in (401, 403):
                            raise LLMError("authentication_error")
                        if status == 429 or 500 <= status <= 599:
                            error = "rate_limited" if status == 429 else "service_unavailable"
                            retryable = True
                            delay = retry_delay(response.headers.get("Retry-After"))
                        elif status != 200:
                            raise LLMError(diagnostic["provider_error"]["classification"] if status == 400 else "http_error")
                        else:
                            content = bytearray()
                            for chunk in response.iter_bytes():
                                if time.monotonic() >= deadline:
                                    raise LLMError("timeout")
                                content.extend(chunk)
                                if len(content) > MAX_RESPONSE_BYTES:
                                    raise LLMError("response_too_large")
                            return self._parse(bytes(content), response_model)
                except httpx.TimeoutException:
                    error, retryable = "timeout", True
                except httpx.NetworkError:
                    error, retryable = "connection_error", True
                except httpx.TransportError:
                    error = "transport_error"
                if not retryable or attempt == 1:
                    raise LLMError(error)
                # Never retry earlier than Retry-After; long delays end this run.
                if delay > MAX_BACKOFF_SECONDS or delay >= deadline - time.monotonic():
                    raise LLMError(error)
                time.sleep(delay)
        raise LLMError("transport_error")

    @staticmethod
    def _parse(content: bytes, response_model: type[T]) -> T:
        try:
            envelope = json.loads(content)
            choice = envelope["choices"][0]
            if choice.get("finish_reason") != "stop":
                raise LLMError("incomplete_response")
            message = choice["message"]
            if message.get("tool_calls") or message.get("refusal"):
                raise LLMError("unsupported_response")
            text = message["content"]
            if not isinstance(text, str):
                raise LLMError("invalid_response")
            parsed = json.loads(text)
        except (ValueError, UnicodeDecodeError):
            raise LLMError("invalid_json") from None
        except (KeyError, IndexError, TypeError, AttributeError):
            raise LLMError("invalid_response") from None
        try:
            return response_model.model_validate(parsed)
        except ValidationError:
            raise LLMError("invalid_structured_output") from None
