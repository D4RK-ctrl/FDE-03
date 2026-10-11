"""Authorized Stage 3 checks only; never prints prompts, bodies, or credentials.

Smoke performs one procurement run. Evaluate executes the unchanged public runner
once, observing only safe per-run metadata, without changing scoring or cases.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
from pathlib import Path
import runpy
import subprocess
import sys
import time

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src import solution
from src.contracts import ProcurementDecision
from src.llm_client import MODEL_NAME


class DiagnosticCollector(logging.Handler):
    def __init__(self):
        super().__init__()
        self.events = []

    def emit(self, record):
        for attribute in ("groq_diagnostic", "decision_diagnostic"):
            if hasattr(record, attribute):
                self.events.append({attribute: getattr(record, attribute)})


class EvaluationPacer:
    """Evaluation-only waiting; never retries a completed case or alters scoring."""
    def __init__(self, interval: float = 20.0, max_wait: float = 300.0):
        if not math.isfinite(interval) or not 0 <= interval <= 60:
            raise ValueError("Evaluation interval must be between 0 and 60 seconds")
        if not math.isfinite(max_wait) or not 0 <= max_wait <= 300:
            raise ValueError("Evaluation wait budget must be between 0 and 300 seconds")
        self.interval, self.max_wait, self.waited = interval, max_wait, 0.0
        self.last_started = None
        self.retry_after = 0.0
        self.daily_limit = False

    def before_run(self):
        if self.daily_limit:
            raise RuntimeError("Evaluation stopped: observed daily limit requires review")
        delay = 0.0 if self.last_started is None else max(0.0, self.last_started + self.interval - time.monotonic())
        delay = max(delay, self.retry_after)
        if delay > 60 or self.waited + delay > self.max_wait:
            raise RuntimeError("Evaluation pacing budget insufficient; no early provider request sent")
        self.waited += delay
        if delay:
            time.sleep(delay)
        self.last_started = time.monotonic()
        self.retry_after = 0.0

    def observe(self, diagnostics):
        for event in diagnostics:
            attempt = event.get("groq_diagnostic", {})
            if attempt.get("http_status") != 429:
                continue
            limits = attempt.get("rate_limits", {})
            observed = limits.get("provider_reported_windows", []) + limits.get("exhausted_header_windows", [])
            self.daily_limit = self.daily_limit or any(x in observed for x in ("requests_per_day", "tokens_per_day"))
            self.retry_after = max(self.retry_after, limits.get("retry_after_seconds", 0.0))


def summarize(request_id, decision, elapsed):
    failed = "ai_analysis_unavailable" in decision.risk_flags
    return {"request_id": request_id, "success": not failed, "model": MODEL_NAME,
            "schema_valid": isinstance(decision, ProcurementDecision),
            "recommendation_category": decision.recommendation.split(". AI advisory assessment:")[0],
            "required_approvals": decision.required_approvals, "risk_flags": decision.risk_flags,
            "tool_calls": decision.telemetry.tool_calls, "llm_calls": decision.telemetry.llm_calls,
            "latency_ms": round(elapsed * 1000, 1),
            "sanitized_error": decision.risk_flags[-1] if failed else None}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("smoke", "evaluate"))
    parser.add_argument("--interval-seconds", type=float, default=20.0)
    parser.add_argument("--max-pacing-seconds", type=float, default=300.0)
    parser.add_argument("--output-prefix", default="STAGE_3B")
    args = parser.parse_args()
    if args.output_prefix != "STAGE_3B":
        parser.error("Correction results must use STAGE_3B; baseline artifacts are protected")
    output = ROOT / "docs" / f"{args.output_prefix}_{args.mode.upper()}_RESULTS.json"
    if output.exists() or output.with_suffix(".csv").exists():
        parser.error("Output already exists; preserve results before another authorized run")
    pacer = EvaluationPacer(args.interval_seconds, args.max_pacing_seconds)
    if not os.getenv("GROQ_API_KEY", "").strip():
        print(json.dumps({"status": "skipped", "error": "missing_api_key", "model": MODEL_NAME}))
        return
    base = os.getenv("VENDOR_RISK_BASE_URL", "http://127.0.0.1:8001").rstrip("/")
    if base != "http://127.0.0.1:8001":
        print(json.dumps({"status": "not_run", "error": "unexpected_mock_base_url"}))
        return
    proc = None
    try:
        try:
            ready = requests.get(base + "/health", timeout=.5).status_code == 200
        except requests.RequestException:
            ready = False
        if not ready:
            proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "mock_api.app:app", "--host", "127.0.0.1", "--port", "8001"],
                                    cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            deadline = time.monotonic() + 10
            while not ready and time.monotonic() < deadline and proc.poll() is None:
                try:
                    ready = requests.get(base + "/health", timeout=.5).status_code == 200
                except requests.RequestException:
                    time.sleep(.25)
            if not ready:
                print(json.dumps({"status": "not_run", "error": "mock_api_startup_failed"}))
                return
        results = []
        original = solution.handle_request
        collector = DiagnosticCollector()
        diagnostic_loggers = [logging.getLogger("src.llm_client"), logging.getLogger("src.decision_validation")]
        prior_levels = [logger.level for logger in diagnostic_loggers]
        for logger in diagnostic_loggers:
            logger.addHandler(collector)
            logger.setLevel(logging.INFO)

        def observed(request_id, architecture="single"):
            if args.mode == "evaluate":
                pacer.before_run()
            collector.events.clear()
            started = time.perf_counter()
            decision = original(request_id, architecture)
            record = summarize(request_id, decision, time.perf_counter() - started)
            record["diagnostics"] = list(collector.events)
            if args.mode == "evaluate":
                pacer.observe(collector.events)
            results.append(record)
            print(json.dumps(record), flush=True)
            return decision

        prior_argv = sys.argv[:]
        csv_path = ROOT / "evals/results_single.csv"
        baseline_csv = csv_path.read_bytes() if csv_path.exists() else None
        try:
            if args.mode == "smoke":
                observed("REQ-1001")
            else:
                solution.handle_request = observed
                sys.argv = [str(ROOT / "evals/run_public_evals.py"), "--architecture", "single"]
                runpy.run_path(str(ROOT / "evals/run_public_evals.py"), run_name="__main__")
                if csv_path.exists():
                    output.with_suffix(".csv").write_bytes(csv_path.read_bytes())
        finally:
            solution.handle_request = original
            sys.argv = prior_argv
            for logger, level in zip(diagnostic_loggers, prior_levels):
                logger.removeHandler(collector)
                logger.setLevel(level)
            if baseline_csv is not None:
                csv_path.write_bytes(baseline_csv)
        output.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)


if __name__ == "__main__":
    main()
