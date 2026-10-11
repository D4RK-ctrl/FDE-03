import unittest
from unittest.mock import patch
from pathlib import Path
import tempfile

from scripts.stage3_live_check import EvaluationPacer, main


class EvaluationPacingTests(unittest.TestCase):
    def test_configured_interval_applies_only_between_runs(self):
        pacer = EvaluationPacer(20, 100)
        with patch("scripts.stage3_live_check.time.monotonic", side_effect=[0, 5, 20]), patch("scripts.stage3_live_check.time.sleep") as sleep:
            pacer.before_run()
            sleep.assert_not_called()
            pacer.before_run()
            sleep.assert_called_once_with(15)
        self.assertEqual(pacer.waited, 15)

    def test_retry_after_is_respected_without_retrying_case(self):
        pacer = EvaluationPacer(0, 100)
        pacer.observe([{"groq_diagnostic": {"http_status": 429, "rate_limits": {"retry_after_seconds": 30}}}])
        with patch("scripts.stage3_live_check.time.sleep") as sleep:
            pacer.before_run()
            sleep.assert_called_once_with(30)

    def test_wait_budget_never_sends_early_request(self):
        pacer = EvaluationPacer(0, 10)
        pacer.observe([{"groq_diagnostic": {"http_status": 429, "rate_limits": {"retry_after_seconds": 30}}}])
        with patch("scripts.stage3_live_check.time.sleep") as sleep, self.assertRaisesRegex(RuntimeError, "budget insufficient"):
            pacer.before_run()
        sleep.assert_not_called()

    def test_daily_exhaustion_stops_further_requests(self):
        pacer = EvaluationPacer()
        pacer.observe([{"groq_diagnostic": {"http_status": 429, "rate_limits": {"provider_reported_windows": ["tokens_per_day"]}}}])
        with self.assertRaisesRegex(RuntimeError, "daily limit"):
            pacer.before_run()

    def test_invalid_configurations_rejected(self):
        for interval, budget in ((-1, 10), (61, 10), (float("nan"), 10), (20, 301), (20, -1)):
            with self.assertRaises(ValueError):
                EvaluationPacer(interval, budget)

    def test_existing_output_cannot_be_overwritten_or_call_network(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "docs").mkdir()
            output = root / "docs/STAGE_3B_EVALUATE_RESULTS.json"
            output.write_text("existing user results")
            with patch("scripts.stage3_live_check.ROOT", root), patch("scripts.stage3_live_check.sys.argv", ["check", "evaluate"]), patch("scripts.stage3_live_check.requests.get") as network, patch("sys.stderr"), self.assertRaises(SystemExit):
                main()
            network.assert_not_called()
            self.assertEqual(output.read_text(), "existing user results")


if __name__ == "__main__":
    unittest.main()
