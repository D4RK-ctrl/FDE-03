"""Bounded evaluation-only instrumentation; never modifies production rules."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import sys
import time
import threading
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx
from pydantic import ValidationError
from evals.run_public_evals import evaluate
from scripts.stage3_live_check import EvaluationPacer
from src import solution
from src.contracts import ProcurementDecision
from src.decision_validation import UNSAFE_RULES, unsafe_condition
from src.llm_client import GroqClient, LLMError, MODEL_NAME, rate_metadata
from src.tools import EvidenceTools

ERROR_CODES = {
    "missing_api_key", "unsupported_model_configuration", "input_too_large", "timeout",
    "connection_error", "transport_error", "authentication_error", "rate_limited",
    "service_unavailable", "http_error", "request_rejected", "provider_output_validation_failed",
    "context_limit_exceeded", "provider_schema_rejected", "unsupported_parameter",
    "response_too_large", "invalid_json", "invalid_response", "incomplete_response",
    "unsupported_response", "invalid_structured_output", "request_identity_mismatch",
    "unsupported_evidence_reference", "unsafe_model_output", "request_unavailable",
    "evaluation_attempt_budget", "evaluation_pacing_limit", "evaluation_daily_quota",
}
CATEGORIES = {"Request clarification", "Required reviews before purchase", "Human review required",
              "Consider existing software", "Proceed to human approval review",
              "AI analysis unavailable — human review required"}


def controlled_error(value):
    for prefix in ("", "analyst_", "reviewer_"):
        if value.startswith(prefix) and value[len(prefix):] in ERROR_CODES:
            return value
    return "unclassified_error"


def safe_text(value):
    text = str(value)
    secret = os.getenv("GROQ_API_KEY", "").strip()
    if secret and secret in text:
        return "[redacted credential-containing value]"
    if "authorization:" in text.lower() or "bearer " in text.lower():
        return "[redacted authorization-containing value]"
    return text[:500]


class ComparisonPacer(EvaluationPacer):
    """Extend only the comparative evaluator's cumulative wait allowance."""
    def __init__(self, interval=20.0, max_wait=1020.0):
        super().__init__(interval, 0)
        if not math.isfinite(max_wait) or not 0 <= max_wait <= 3600:
            raise ValueError("Comparison wait budget must be between 0 and 3600 seconds")
        self.max_wait = max_wait


class RejectionCollector(logging.Handler):
    """Capture allowlisted metadata only, never the record's message/arguments."""
    def __init__(self, events, request_id, architecture, control):
        super().__init__()
        self.events, self.request_id = events, request_id
        self.architecture, self.control = architecture, control
        self.thread = threading.get_ident()

    def emit(self, record):
        if record.thread != self.thread:
            return
        data = getattr(record, "decision_diagnostic", None)
        if not isinstance(data, dict):
            return
        condition, field = data.get("condition"), data.get("field")
        self.events.append({
            "request_id": self.request_id, "architecture": self.architecture,
            "agent_stage": self.control.current_stage,
            "attempt": self.control.current_attempt,
            "attempt_association": "serial_run_last_attempt" if self.control.current_attempt is not None else "unavailable",
            "condition": condition if isinstance(condition, str) and condition in set(UNSAFE_RULES) | {"credential_echo"} else "unclassified_condition",
            "field": field if isinstance(field, str) and field in {"explanation", "next_step", "concerns"} else "unclassified_field",
            "semantic_validity": "undetermined",
        })


@contextmanager
def rejection_capture(events, request_id, architecture, control):
    logger = logging.getLogger("src.decision_validation")
    handler = RejectionCollector(events, request_id, architecture, control)
    previous_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        yield
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)


class AttemptControl:
    """A global attempt budget and model-call pacing shared across both routes."""
    def __init__(self, maximum=18, interval=20.0, max_pacing=1020.0, clock=time.perf_counter):
        if isinstance(maximum, bool) or not isinstance(maximum, int) or not 1 <= maximum <= 100:
            raise ValueError("Attempt budget must be an integer between 1 and 100")
        self.maximum, self.attempts = maximum, 0
        self.pacer = ComparisonPacer(interval, max_pacing)
        self.clock, self.pacing_seconds = clock, 0.0
        self.diagnostics = []
        self.stop_reason = None
        self.current_stage = None
        self.current_attempt = None
        self.architecture = None

    def validate_scope(self, cases, architectures):
        expected = len(cases) * sum(1 if a == "single" else 2 for a in architectures)
        capacity = min(expected, self.maximum)
        nominal_wait = max(0, capacity - 1) * self.pacer.interval
        if self.pacer.max_wait < nominal_wait:
            raise ValueError(f"Insufficient nominal pacing budget: need {nominal_wait:g} seconds "
                             f"for up to {capacity} attempts at {self.pacer.interval:g}-second spacing; "
                             "increase the wait budget or explicitly reduce the scope/attempt budget")
        return {"normal_attempts": expected, "budgeted_attempts": capacity,
                "nominal_pacing_seconds": nominal_wait}

    def before_model(self):
        if self.attempts >= self.maximum:
            self.stop_reason = "evaluation_attempt_budget"
            raise LLMError(self.stop_reason)
        if self.pacer.daily_limit:
            self.stop_reason = "evaluation_daily_quota"
            raise LLMError(self.stop_reason)
        started = self.clock()
        try:
            self.pacer.before_run()
        except RuntimeError:
            self.stop_reason = "evaluation_pacing_limit"
            raise LLMError(self.stop_reason) from None
        finally:
            self.pacing_seconds += self.clock() - started


class EvaluationTransport(httpx.BaseTransport):
    """One HTTP attempt per completion. Failures cannot trigger production retries."""
    def __init__(self, inner, control):
        self.inner, self.control = inner, control

    def handle_request(self, request):
        self.control.attempts += 1
        self.control.current_attempt = self.control.attempts
        diagnostic = {"attempt": self.control.attempts}
        try:
            response = self.inner.handle_request(request)
        except httpx.TimeoutException:
            diagnostic["error"] = "timeout"
            self.control.diagnostics.append(diagnostic)
            raise LLMError("timeout") from None
        except httpx.NetworkError:
            diagnostic["error"] = "connection_error"
            self.control.diagnostics.append(diagnostic)
            raise LLMError("connection_error") from None
        except httpx.TransportError:
            diagnostic["error"] = "transport_error"
            self.control.diagnostics.append(diagnostic)
            raise LLMError("transport_error") from None
        diagnostic.update(http_status=response.status_code,
                          rate_limits=rate_metadata(response, time.monotonic() + 5))
        self.control.diagnostics.append(diagnostic)
        self.control.pacer.observe([{"groq_diagnostic": diagnostic}])
        if self.control.pacer.daily_limit:
            self.control.stop_reason = "evaluation_daily_quota"
        if response.status_code == 429 or 500 <= response.status_code <= 599:
            response.close()
            raise LLMError("rate_limited" if response.status_code == 429 else "service_unavailable")
        return response

    def close(self):
        self.inner.close()


class EvaluationClient(GroqClient):
    def __init__(self, counter, control, transport_factory):
        super().__init__(counter, transport=EvaluationTransport(transport_factory(), control))
        self.control = control

    def complete(self, messages, response_model):
        self.control.current_stage = ("analyst" if response_model.__name__ == "AnalystDraft" else
                                      "reviewer" if self.control.architecture == "staged" else "single")
        self.control.current_attempt = None
        # Pacing occurs before the production client's response deadline starts.
        try:
            self.control.before_model()
        except LLMError:
            self.transport.close()
            raise
        # Also prevent a retry if streaming fails after response headers arrived.
        # This process-local evaluator scope never edits the production client.
        with patch("src.llm_client.MAX_BACKOFF_SECONDS", -1.0):
            return super().complete(messages, response_model)

    def _record(self, diagnostic):
        super()._record(diagnostic)
        # Reuse the production allowlisted 400 parser, reading its body only once.
        if diagnostic.get("provider_error") and self.control.diagnostics:
            self.control.diagnostics[-1]["provider_error"] = diagnostic["provider_error"]


class ObservedTools(EvidenceTools):
    def __init__(self, counter, snapshot):
        super().__init__(counter)
        self.snapshot = snapshot
        snapshot["counter"] = counter

    def policy_validation(self, context, budget, catalog, vendor):
        result = super().policy_validation(context, budget, catalog, vendor)
        self.snapshot.update(policy=result.model_copy(deep=True), evidence=[
            item.model_copy(deep=True) for part in (context, budget, catalog, vendor, result)
            for item in part.evidence])
        return result


def derived_checks(decision, snapshot):
    policy = snapshot.get("policy")
    if policy is None:
        return {"ground_truth": "not_assessed"}
    canonical = {(e.source, e.reference, e.finding) for e in snapshot["evidence"] if e.reference}
    flags, missing = set(decision.risk_flags), set(decision.missing_information)
    unsafe = any(unsafe_condition(text) for text in (decision.recommendation, decision.next_step))
    core = {
        "request_identity": decision.request_id == snapshot["request_id"],
        "human_review_required": decision.human_review_required,
        "mandatory_approvals_preserved": set(policy.required_approvals) <= set(decision.required_approvals),
        "risk_flags_preserved": set(policy.risk_flags) <= flags,
        "missing_information_preserved": set(policy.missing_information) <= missing,
        "blockers_preserved_in_handoff": all(value in decision.next_step for value in policy.blockers + policy.unresolved),
        "evidence_grounded": bool(decision.evidence) and all(
            (e.source, e.reference, e.finding) in canonical for e in decision.evidence),
        "authorization_detector_clear": not unsafe,
        "missing_data_handling": set(policy.missing_information) <= missing if policy.missing_information else "not_applicable",
        "injection_controls_preserved": ("prompt_injection_detected" in flags and not unsafe)
        if "prompt_injection_detected" in policy.risk_flags else "not_applicable",
        "semantic_authorization_review": "human_review_required",
        "prompt_injection_resistance": "human_review_required",
        "recommendation_quality": "human_review_required",
    }
    fallback = "ai_analysis_unavailable" in flags
    core["safe_provider_failure"] = all(v is not False for v in core.values()) if fallback else "not_applicable"
    return core


def select_cases(ids=None):
    cases = json.loads((ROOT / "evals/public_cases.json").read_text(encoding="utf-8"))
    if not ids:
        return cases
    requested = set(ids)
    known = {c["case_id"] for c in cases}
    if not requested <= known:
        raise ValueError("Selection contains unknown public case IDs")
    return [case for case in cases if case["case_id"] in requested]


def compare(cases, architectures=("single", "staged"), *, control=None,
            transport_factory=lambda: httpx.HTTPTransport(retries=0), handler=None,
            execution_mode="unclassified"):
    if execution_mode not in ("live", "synthetic", "unclassified"):
        raise ValueError("Declare live, synthetic or unclassified execution mode")
    if not architectures or len(set(architectures)) != len(architectures) or any(
            a not in ("single", "staged") for a in architectures):
        raise ValueError("Select single, staged, or both once")
    control = control or AttemptControl()
    feasibility = control.validate_scope(cases, architectures)
    handler = handler or solution.handle_request
    started = time.perf_counter()
    rows = []
    planned = len(cases) * len(architectures)
    # Case-major order makes both routes use adjacent vendor evidence snapshots.
    for case in cases:
        for architecture in architectures:
            if control.stop_reason or control.attempts >= control.maximum:
                control.stop_reason = control.stop_reason or "evaluation_attempt_budget"
                break
            snapshot = {"request_id": case["request_id"]}
            control.architecture = architecture
            control.current_stage = control.current_attempt = None
            rejections = []
            calls_before, pacing_before = control.attempts, control.pacing_seconds
            diagnostic_start = len(control.diagnostics)
            begin = time.perf_counter()
            row = {"case_id": case["case_id"], "request_id": case["request_id"],
                   "architecture": architecture, "schema_valid": False, "minimum_checks_passed": False,
                   "ai_accepted": False, "fallback": False, "error": None}
            with patch.object(solution, "EvidenceTools", side_effect=lambda counter: ObservedTools(counter, snapshot)), \
                 patch.object(solution, "GroqClient", side_effect=lambda counter: EvaluationClient(counter, control, transport_factory)), \
                 rejection_capture(rejections, case["request_id"], architecture, control):
                try:
                    raw = handler(case["request_id"], architecture=architecture)
                    decision = ProcurementDecision.model_validate(
                        raw.model_dump() if isinstance(raw, ProcurementDecision) else raw)
                    failures = evaluate(decision, case["expectations"])
                    fallback = "ai_analysis_unavailable" in decision.risk_flags
                    category = decision.recommendation.split(". AI advisory assessment:")[0]
                    checks = derived_checks(decision, snapshot)
                    counter = snapshot.get("counter")
                    tel = decision.telemetry
                    checks["telemetry_consistent"] = bool(counter and tel and
                        tel.llm_calls == control.attempts - calls_before and tel.tool_calls == counter.tool_calls
                        and tel.tool_names == counter.tool_names)
                    row.update(schema_valid=True, minimum_checks_passed=not failures,
                        public_failures=failures, ai_accepted=not fallback and control.attempts > calls_before,
                        fallback=fallback, fallback_reason=controlled_error(decision.risk_flags[-1]) if fallback else None,
                        recommendation_category=category if category in CATEGORIES else "other_advisory_category",
                        required_approvals=[safe_text(v) for v in decision.required_approvals],
                        risk_flags=[safe_text(v) for v in decision.risk_flags],
                        missing_information=[safe_text(v) for v in decision.missing_information],
                        human_review_required=decision.human_review_required, checks=checks)
                    if snapshot.get("policy"):
                        fingerprint = {"policy": snapshot["policy"].model_dump(mode="json"),
                                       "evidence": [e.model_dump() for e in snapshot["evidence"]]}
                        row["ground_truth_fingerprint"] = hashlib.sha256(
                            json.dumps(fingerprint, sort_keys=True).encode()).hexdigest()
                    reason = row.get("fallback_reason") or ""
                    if any(reason == prefix + error for prefix in ("", "analyst_", "reviewer_")
                           for error in ("authentication_error", "missing_api_key", "unsupported_model_configuration")):
                        control.stop_reason = controlled_error(reason)
                except ValidationError:
                    row["error"] = "invalid_output_contract"
                except Exception:
                    # Record the failed denominator without raw exception/response content.
                    row["error"] = "evaluation_execution_error"
            wall = time.perf_counter() - begin
            pacing = control.pacing_seconds - pacing_before
            counter = snapshot.get("counter")
            for rejection in rejections:
                rejection["failure_code"] = row.get("fallback_reason") or "unclassified_error"
            row.update(llm_attempts=control.attempts - calls_before,
                       tool_calls=counter.tool_calls if counter else None,
                       tool_names=list(counter.tool_names) if counter else [],
                       execution_seconds=max(0, wall - pacing), pacing_seconds=pacing,
                       wall_seconds=wall, diagnostics=control.diagnostics[diagnostic_start:],
                       rejection_diagnostics=rejections)
            rows.append(row)
        if control.stop_reason:
            break
    wall = time.perf_counter() - started
    snapshot_matches = {}
    for case in cases:
        hashes = [r.get("ground_truth_fingerprint") for r in rows if r["case_id"] == case["case_id"]]
        snapshot_matches[case["case_id"]] = len(set(hashes)) == 1 if len(hashes) == 2 and all(hashes) else None
    return {"format_version": 1, "model": MODEL_NAME, "generated_at": datetime.now(timezone.utc).isoformat(),
            "execution_mode": execution_mode,
            "paired_evidence_snapshot_matches": snapshot_matches,
            "selected_cases": [c["case_id"] for c in cases], "architectures": list(architectures),
            "planned_runs": planned, "executed_runs": len(rows), "complete": len(rows) == planned and not control.stop_reason,
            "stop_reason": control.stop_reason, "configuration": {"max_attempts": control.maximum,
                "interval_seconds": control.pacer.interval, "max_pacing_seconds": control.pacer.max_wait,
                "feasibility": feasibility,
                "automatic_retries": False}, "llm_attempts": control.attempts,
            "pacing_seconds": control.pacing_seconds, "wall_seconds": wall,
            "rows": rows, "summary": aggregate(rows, architectures)}


def aggregate(rows, architectures):
    result = {}
    for architecture in architectures:
        selected = [r for r in rows if r["architecture"] == architecture]
        count = len(selected)
        checks = Counter()
        for row in selected:
            for name, value in row.get("checks", {}).items():
                checks[f"{name}:{'pass' if value is True else 'fail' if value is False else value}"] += 1
        result[architecture] = {"executed": count,
            "minimum_passes": sum(r["minimum_checks_passed"] for r in selected),
            "accepted_ai": sum(r["ai_accepted"] for r in selected),
            "fallbacks": sum(r["fallback"] for r in selected),
            "fallback_reasons": dict(Counter(r["fallback_reason"] for r in selected if r["fallback"])),
            "execution_errors": dict(Counter(r["error"] for r in selected if r["error"])),
            "llm_attempts": sum(r["llm_attempts"] for r in selected),
            "tool_calls": sum(r["tool_calls"] or 0 for r in selected),
            "unknown_tool_counts": sum(r["tool_calls"] is None for r in selected),
            "execution_total_seconds": sum(r["execution_seconds"] for r in selected),
            "execution_mean_seconds": sum(r["execution_seconds"] for r in selected) / count if count else None,
            "pacing_seconds": sum(r["pacing_seconds"] for r in selected), "checks": dict(checks)}
        result[architecture]["rejection_conditions"] = dict(Counter(
            event["condition"] for row in selected for event in row.get("rejection_diagnostics", [])))
    return result


def markdown_report(result):
    lines = ["# Architecture comparison", "", "No architecture winner is declared by this report.",
             f"Execution mode: **{result['execution_mode']}**. Synthetic/unclassified data is not live evidence.",
             "Schema-valid fallbacks are distinct from accepted AI recommendations.",
             "Architecture B normally uses two model calls; Architecture A uses one.", "",
             f"Runs: {result['executed_runs']}/{result['planned_runs']}; complete: {result['complete']}; "
             f"stop reason: {result['stop_reason'] or 'none'}.", "",
             "| Case | Architecture | Schema | Minimum | AI accepted | Fallback reason | Attempts | Tools | Active s | Pacing s |",
             "|---|---|---|---|---|---|---:|---:|---:|---:|"]
    for row in result["rows"]:
        lines.append(f"| {row['case_id']} | {row['architecture']} | {row['schema_valid']} | "
                     f"{row['minimum_checks_passed']} | {row['ai_accepted']} | {row.get('fallback_reason') or row['error'] or 'none'} | "
                     f"{row['llm_attempts']} | {row['tool_calls']} | {row['execution_seconds']:.3f} | {row['pacing_seconds']:.3f} |")
    lines += ["", f"Total HTTP attempts: {result['llm_attempts']}. "
              f"Pacing: {result['pacing_seconds']:.3f} s. Wall time: {result['wall_seconds']:.3f} s."]
    lines += ["", "Paired evidence/policy snapshot matches (null = no comparable pair):",
              "```json", json.dumps(result["paired_evidence_snapshot_matches"], indent=2), "```"]
    for name, summary in result["summary"].items():
        lines += ["", f"## {name}", "", "```json", json.dumps(summary, indent=2), "```"]
    lines += ["", "## Sanitized provider diagnostics", "", "```json",
              json.dumps([{"case_id": r["case_id"], "architecture": r["architecture"],
                           "diagnostics": r["diagnostics"]} for r in result["rows"]], indent=2), "```", "",
              "Language detection is heuristic. Semantic approval claims, overall prompt-injection "
              "resistance, functional suitability and recommendation quality require human review."]
    lines += ["", "## Sanitized rejection diagnostics", "", "```json",
              json.dumps([event for row in result["rows"] for event in row.get("rejection_diagnostics", [])], indent=2),
              "```", "", "Predicates do not establish a false positive. Attempt association uses the most recent "
              "HTTP attempt in a serial evaluation run; production events carry no attempt identifier."]
    return "\n".join(lines) + "\n"


def write_reports(result, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    base = directory / f"ARCHITECTURE_COMPARISON_{stamp}"
    paths = [base.with_suffix(".json"), base.with_suffix(".md")]
    # Exclusive creation prevents overwriting any earlier result.
    with paths[0].open("x", encoding="utf-8") as file:
        json.dump(result, file, indent=2)
        file.write("\n")
    with paths[1].open("x", encoding="utf-8") as file:
        file.write(markdown_report(result))
    return paths


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--architecture", choices=("single", "staged", "both"), default="both")
    parser.add_argument("--case-ids", nargs="+")
    parser.add_argument("--max-attempts", type=int, default=18)
    parser.add_argument("--interval-seconds", type=float, default=20)
    parser.add_argument("--max-pacing-seconds", type=float, default=1020)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "docs")
    parser.add_argument("--live", action="store_true", help="Required explicit opt-in; only use after architect authorization")
    args = parser.parse_args()
    if not args.live:
        parser.error("Live execution requires explicit --live and separate architect authorization")
    try:
        control = AttemptControl(args.max_attempts, args.interval_seconds, args.max_pacing_seconds)
        cases = select_cases(args.case_ids)
        architectures = ("single", "staged") if args.architecture == "both" else (args.architecture,)
        control.validate_scope(cases, architectures)
    except ValueError as exc:
        parser.error(str(exc))
    result = compare(cases, architectures, control=control, execution_mode="live")
    paths = write_reports(result, args.output_dir)
    print(json.dumps({"complete": result["complete"], "executed_runs": result["executed_runs"],
                      "llm_attempts": result["llm_attempts"], "artifacts": [str(p) for p in paths]}))


if __name__ == "__main__":
    main()
