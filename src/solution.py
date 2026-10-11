from __future__ import annotations

import logging
import time

from src.contracts import Architecture, ProcurementDecision
from src.agents import single_agent
from src.decision_validation import attach_telemetry, enforce_decision, safe_handoff
from src.llm_client import GroqClient, LLMError
from src.telemetry import RunTelemetryCounter
from src.tools import EvidenceTools

logger = logging.getLogger(__name__)


def handle_request(request_id: str, architecture: Architecture = "single") -> ProcurementDecision:
    """Assessment adapter.

    Keep this function callable by the public/hidden evaluation harness.
    Your internal implementation may use any framework, modules, agents, tools,
    deterministic checks, or orchestration strategy.
    """
    if architecture == "staged":
        raise NotImplementedError("Staged architecture awaits Stage 4 implementation.")
    if architecture != "single":
        raise ValueError("Unsupported architecture; expected 'single' or 'staged'.")
    if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 200:
        raise ValueError("request_id must be a nonempty string of at most 200 characters")
    started = time.perf_counter()
    counter = RunTelemetryCounter()
    tools = EvidenceTools(counter)
    context = tools.request_context(request_id)
    # Even an unknown request receives a schema-valid handoff and policy checks.
    budget = tools.budget(context)
    catalog = tools.catalog_history(context)
    vendor = tools.vendor_security(context)
    policy = tools.policy_validation(context, budget, catalog, vendor)
    evidence = [e for result in (context, budget, catalog, vendor, policy) for e in result.evidence]
    client = GroqClient(counter)
    error = "none"
    if not context.request:
        error = "request_unavailable"
        decision = safe_handoff(request_id, policy, evidence, error)
    else:
        try:
            advice = single_agent(client, context, budget, catalog, vendor, policy)
        except LLMError as exc:
            error = exc.code
            decision = safe_handoff(request_id, policy, evidence, error)
        else:
            decision = enforce_decision(request_id, advice, policy, evidence)
            if "ai_analysis_unavailable" in decision.risk_flags:
                error = decision.risk_flags[-1]
    decision = attach_telemetry(decision, counter)
    # Public telemetry has no latency/model/error fields. Emit only controlled
    # diagnostics through logging; the unchanged evaluator also measures latency.
    logger.info("procurement_run model=%s elapsed_ms=%.1f llm_calls=%d tool_calls=%d error=%s",
                "openai/gpt-oss-120b", (time.perf_counter() - started) * 1000,
                counter.llm_calls, counter.tool_calls, error)
    return decision
