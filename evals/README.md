# Evaluation guide

Run commands from the repository root in the installed Python 3.12 environment. Offline tests mock HTTP transports:

```powershell
python -m unittest discover -s tests -v
```

## Public minimum checks

The six cases and expectations in [public_cases.json](public_cases.json) remain unchanged. The runner calls `handle_request`, validates the contract, checks minimum expectations and exports CSVs:

```powershell
python evals/run_public_evals.py --architecture single
python evals/run_public_evals.py --architecture staged
```

These commands call Groq through production code, require a configured key and running vendor API, and overwrite architecture-specific `evals/results_*.csv` files. Preserve earlier results first. They use production retries without comparison pacing. Passing minimum checks, including a passing fallback, does not establish successful AI analysis or semantic correctness.

## Controlled comparison

Obtain architect authorization for live calls. Use the API started by `python run_local.py`, or start only the API in a separate terminal:

```powershell
python -m uvicorn mock_api.app:app --host 127.0.0.1 --port 8001
```

Do not start duplicate services. With sufficient quota, run:

```powershell
python evals/compare_architectures.py --live --architecture both --max-attempts 18 --interval-seconds 60 --max-pacing-seconds 1200
```

`--architecture` accepts `single`, `staged` or `both`. `--case-ids` selects a subset; omission selects all six. `--max-attempts` bounds global HTTP attempts. The runner disables retries, validates pacing allowance against planned scope, stops on blocking provider conditions, and exclusively creates timestamped JSON/Markdown reports under `docs/` (or `--output-dir`). Keep incomplete runs partial; do not fill missing results or automatically rerun failures.

Reports distinguish schema validity, public minimum checks, accepted advice, fallbacks, mandatory approvals/risks/missing information, evidence grounding, human review, attempts, tools, active latency and pacing. Paired evidence/policy snapshot checks assess consistent ground truth. Sanitized diagnostics omit prompts, business records and credentials. Semantic checks are heuristic and need independent human review.

## Preserved final evidence

Stage 6D: [JSON](../docs/ARCHITECTURE_COMPARISON_20261011_132721_049614.json), [Markdown](../docs/ARCHITECTURE_COMPARISON_20261011_132721_049614.md). Both architectures passed 6/6 minimum checks. A accepted 4/6 AI outputs with two safe fallbacks; B accepted 5/6 with one. A used 6 HTTP attempts, B 12; both used 30 tools. Three safety rejections occurred with no provider HTTP errors.

Mean active latency was 1.890 s for A and 3.584 s for B; total active latency was 11.337 s and 21.502 s. Pacing was 989.490 s; wall time was 1022.331 s. Do not conflate pacing with application latency or treat six cases as conclusive. See the [decision memo](../docs/ARCHITECTURE_DECISION.md). Preserve historical reports.
