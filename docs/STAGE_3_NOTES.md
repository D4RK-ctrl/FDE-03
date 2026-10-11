# Stage 3 handoff: Architecture A

Status: **PARTIAL**. Architecture A is implemented and offline checks pass. The single live smoke succeeded. Public minimum checks passed 6/6, but accepted live AI advice was obtained for only 2/6 public cases: one response was rejected by the safety guard and three encountered rate limits. Those four cases returned explicit deterministic human handoffs. This is not a six-case successful live-model evaluation.

Branch: `codex/stage-3-single-agent`, created from the latest Stage 2 branch. Existing work/resources were preserved. All changes remain local; no git staging, commit, push, merge, or PR occurred. Stage 4 was neither implemented nor evaluated.

## Implementation

- `src/llm_client.py`: existing httpx dependency, fixed endpoint `https://api.groq.com/openai/v1/chat/completions`, environment key, default/fixed model `openai/gpt-oss-120b`. Nonempty configuration for another model is rejected instead of silently switching providers/models.
- `src/agents.py`: one agent receiving original request/context, budget, catalog/history, vendor registry/API, and shared policy result. Business records remain in a user-role JSON envelope labeled untrusted; policy authority is defined by application instructions. There is no model tool execution, arbitrary retrieval, file access, or execution facility.
- `src/decision_validation.py`: validates bounded `AgentAdvice`, request identity, and evidence references. Final approvals/flags/missing information are copied from `PolicyResult`; blockers and unresolved conditions are rendered explicitly in the next step. Factual evidence is copied only from tool/policy findings, never from model-generated findings. Unsupported references reject the advice rather than creating evidence.
- `src/solution.py`: exact public signature retained; one per-run EvidenceTools instance invokes the five tools once, calls the single agent, enforces the decision, and attaches existing telemetry. Unknown requests obtain a schema-valid unavailable-request handoff with zero model calls. Unsupported architecture values are rejected; `staged` remains unimplemented.
- `scripts/stage3_live_check.py`: bounded-validation helper. It starts/reuses the local mock HTTP service, cleans up only its own process, performs one smoke or one public-evaluation run, and writes sanitized summaries. Evaluation observes adapter return metadata and invokes the unchanged `evals/run_public_evals.py` through `runpy` with `--architecture single`. It does not modify cases, expectations, scoring, or agent behavior.

Internal structured advice contains request ID, an advisory category, concise explanation, a suggested next step, and source references. It deliberately has no approval, human-control, or factual-evidence-authoring fields. The final result is validated against the unchanged `ProcurementDecision` contract.

## Provider integration and bounds

Provider format was checked against official documentation:

- [Groq structured outputs](https://console.groq.com/docs/structured-outputs): the selected model supports strict JSON schema; required fields and `additionalProperties: false` are used. Length bounds are checked locally, with unsupported schema constraints removed from the provider schema.
- [Groq API reference](https://console.groq.com/docs/api-reference): chat-completions endpoint, `max_completion_tokens`, and selected model's `reasoning_effort=low` were verified.
- [Groq rate limits](https://console.groq.com/docs/rate-limits): HTTP 429 and Retry-After handling were verified.

Limits: 48,000 serialized input bytes including schema; 1,800 completion tokens; 64,000 response bytes; connection/write/pool timeouts 5 seconds each and read timeout 15 seconds. A monotonic 45-second budget is checked before attempts and while receiving body chunks. Synchronous HTTP phase timeouts can extend the wall-clock deadline while an operation is blocked; validation commands additionally use process bounds (65 seconds for smoke, 360 seconds for six-case evaluation). No SDK, automatic retry, redirect following, or new dependency is used.

At most one retry is allowed for 429/5xx or network timeout/failure. Backoff is at most 2 seconds. A longer Retry-After ends the request without retrying early. Failed attempts count as actual LLM calls. Authentication, rejected requests, invalid JSON/schema, refusals, truncated output, oversized input/response, identity mismatch, and other transport failures are explicit controlled error states. HTTP bodies, headers, prompts, and credentials are not printed or incorporated into errors.

## Enforcement and handoffs

Material missing fields override favorable AI categories. Mandatory policy blockers force review. Human review is always true. Every next step starts with human authorization requirements and lists applicable approvals/clarifications/unresolved blockers.

A conservative text guard rejects bypass/waiver instructions, assertions of completed approvals/purchase authorization, and echoed credentials. Rejected or unavailable advice becomes `AI analysis unavailable — human review required` with `ai_analysis_unavailable` and a controlled error flag. This is explicitly a failure handoff, not successful AI reasoning. It preserves all deterministic approvals, risks, missing information, and grounded findings.

The text guard is heuristic and conservative: it can reject legitimate phrasing and cannot prove every natural-language assertion. The deterministic fields and advisory categories remain application-owned, and there is no purchasing action. Model explanations are labeled advisory, not inserted as factual evidence. Architect review should assess this limitation before broader deployment.

## Live evidence (no repeat attempts to improve results)

Exactly one smoke procurement run was performed after offline tests passed:

| Request | Outcome | Model | Schema | Approvals | Flags | Tools | LLM attempts | Latency |
|---|---|---|---|---|---|---:|---:|---:|
| REQ-1001 | Success; Human review required | openai/gpt-oss-120b | Valid | Manager | existing_tool_overlap | 5 | 1 | 2,414.5 ms |

One run of the unchanged six public cases for `single` produced:

| Case / request | Public minimum checks | Accepted AI advice | Failure/handoff | Tools | LLM attempts | Latency |
|---|---|---|---|---:|---:|---:|
| PUB-01 / REQ-1001 | PASS | Yes | None | 5 | 1 | 1,901.8 ms |
| PUB-02 / REQ-1002 | PASS | No | unsafe_model_output | 5 | 1 | 2,162.1 ms |
| PUB-03 / REQ-1003 | PASS | Yes | None | 5 | 1 | 1,744.9 ms |
| PUB-04 / REQ-1005 | PASS | No | rate_limited | 5 | 1 | 583.6 ms |
| PUB-05 / REQ-1006 | PASS | No | rate_limited | 5 | 1 | 430.7 ms |
| PUB-06 / REQ-1009 | PASS | No | rate_limited; vendor_risk_unavailable also preserved | 5 | 1 | 533.1 ms |

All outputs were schema-valid. Mean end-to-end latency was 1,226.0 ms across all six cases, including failures; this is not successful-model latency. Rate-limited runs made one HTTP attempt each because the required retry wait did not fit the bounded backoff. Exact Retry-After durations were not retained. No alternate model, early retry, delayed second evaluation, or hardcoded response was used.

The safety-rejected response was not retained; its wording is not available to determine whether it was unsafe or a conservative false positive. Do not claim more than the recorded rejection category. Public checks do not measure model-advice success, so their 6/6 result must be reported alongside 2/6 accepted AI outputs.

Artifacts: `docs/STAGE_3_SMOKE_RESULTS.json`, `docs/STAGE_3_EVALUATE_RESULTS.json`, and ignored `evals/results_single.csv`. They contain only permitted summary information, not full prompts/responses or keys.

## Verification and remaining review

Commands: repository/file inspection and requested branch creation; `.venv\Scripts\python.exe -m pip check`; bounded `verify_setup.py`; focused/full `unittest discover -s tests -v`; smoke helper; evaluator helper (unchanged public runner with `--architecture single`); AST parsing; Git diff/status and new-file whitespace checks.

The first focused client suite had 14 passes and 1 failure caused by a test helper searching for the empty API-key string in an error. The assertion was corrected to check leakage only for nonempty credentials. Enforcement tests passed 9/9 and single-agent tests passed 10/10 in that run. Subsequent full offline testing passed 92/92 before live calls. Additional timeout/default-model/logging tests were added during final review; final results are recorded below.

Sandbox preflight again stalled at TestClient and ended with `subprocess.TimeoutExpired` after 20 seconds. Outside-sandbox bounded preflight passed with `PRE-FLIGHT PASSED`. The unchanged TestClient/httpx deprecation warning remains non-blocking. `pip check` reported no broken requirements. No browser testing occurred.

Final offline result: **95 passed, 0 failed, 0 skipped**, in 0.775 seconds (58 pre-existing tests, 17 client tests, 9 enforcement tests, 11 single-agent tests). Syntax/whitespace checks passed, including checks for new untracked files. Final tracked diff modifies only `src/solution.py`; other Stage 3 files are new and untracked. Business datasets, procurement policy, Stage 2 engine/tools, public contracts, launchers/UI, dependencies, and public evaluator/cases remain unchanged.

Outstanding issues: provider token/rate capacity for the six-case workload; conservative safety-guard rejection/false positives; advisory natural-language grounding beyond canonical evidence; and timeout deadline overshoot during blocked synchronous I/O. None were concealed by the public minimum-check score. Await architect review; do not start staged agents or UI work.
