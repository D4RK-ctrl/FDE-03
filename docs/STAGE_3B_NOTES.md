# Stage 3B: reliability corrections and diagnosis

Branch remains `codex/stage-3-single-agent`. Offline corrections are complete. The exact historical safety match and provider rate-limit subtype remain unproven because those diagnostics were not retained. No live Groq calls, live smoke, or live public evaluation were performed in this task. No commits, pushes, merges, staged agents, UI changes, or dataset/policy/evaluation-expectation changes occurred.

## Safety investigation

The retained PUB-02 record says only `unsafe_model_output`. In the baseline validator, that code could come from either credential-echo detection or the broad regex. The rejected explanation/next step, matched substring, field, and predicate were not saved. Therefore it is impossible to identify the exact historical condition or to label the original response harmless/unsafe with the retained evidence. This limitation is not resolved by guessing a plausible response.

Confirmed code defect, reproduced offline: the old regex ignored negation and searched across sentences and the explanation/next-step boundary. It rejected conservative wording such as `Do not purchase now`, `Do not bypass approvals`, and future review prerequisites. It could also mistake an approved vendor mentioned near a proposed purchase for purchase authorization.

Correction: validate explanation and next step independently; use named predicates for bypasses, purchase authorization/actions, waived reviews, and completed approvals. Mask tightly scoped explicit negative actions/statuses and future approval prerequisites. Other clauses remain checked, so `Do not buy now, but skip Security review` and a negative sentence followed by an affirmative authorization claim still fail. The matcher remains conservative and heuristic, not a complete natural-language safety proof.

Future rejections log only a controlled condition name and field (`explanation`/`next_step`). Credentials, business wording, and provider response text are excluded. This makes the next observed rejection diagnosable without retaining the response. Evidence-reference verification, public schema validation, policy-owned approvals/flags/missing information, blockers, and required human review were not relaxed.

## Rate-limit investigation

Three baseline cases had `rate_limited` and one actual model attempt each. The raw 429 response, Retry-After, quota counters/reset windows, and provider error message were not retained. We cannot distinguish RPM, TPM, RPD, TPD, organization-wide usage, or another provider constraint from those summaries alone. Rapid sequential invocation and duplicate prompt text are workload characteristics, not proof of the rate-limit subtype.

[Groq's official rate-limit documentation](https://console.groq.com/docs/rate-limits) distinguishes minute/day and input/output limits. It specifies request quota headers as **requests per day** and token quota headers as **tokens per minute**. Header names containing `requests` must not be relabeled RPM. Published defaults are not evidence of this user's actual account limits.

The client now retains/logs safe per-response attempt metadata: attempt index, HTTP status, input byte count, completion cap, validated numeric quota counters, validated reset durations, and bounded Retry-After seconds. For 429 bodies, it extracts only recognized window names from a bounded error message; it never retains the message, organization ID, headers containing credentials, or arbitrary text. Explicit provider-reported windows and exhausted header windows are separate fields. Missing evidence remains unknown. Malformed/failed optional diagnostic parsing cannot conceal or replace the original HTTP 429.

Production behavior remains at most two actual HTTP attempts, at most two seconds of backoff, and the existing response/connect timeout and deadline checks. A long Retry-After does not trigger an early retry. No long evaluation sleep was added to a product request.

## Payload reduction and evaluation pacing

The prompt retains original request/context, Decimal budget evidence, candidate scope/seats and notes, history, vendor registry/API records and conflicts, and all deterministic policy findings/checks/flags/approvals/blockers/unresolved conditions. Only repeated per-tool evidence prose is omitted from the prompt; canonical output evidence remains unchanged and all source references remain supplied. Compact JSON removes whitespace. The completion cap is reduced from 1,800 to 1,200 tokens; model remains `openai/gpt-oss-120b`.

Offline mocked measurements of the business-data envelope (not actual tokenizer counts or live token usage):

| Request | Baseline bytes | Compact bytes | Reduction |
|---|---:|---:|---:|
| REQ-1001 | 5,193 | 3,550 | 31.6% |
| REQ-1002 | 7,332 | 5,190 | 29.2% |
| REQ-1003 | 5,691 | 4,017 | 29.4% |
| REQ-1005 | 5,928 | 4,706 | 20.6% |
| REQ-1006 | 7,299 | 5,579 | 23.6% |
| REQ-1009 | 5,754 | 4,599 | 20.1% |

Aggregate envelope reduction: 25.7%; completion cap reduction: 33.3%. These numbers do not prove rate-limit resolution or adequacy of the reduced output cap on live reasoning.

The evaluation helper now has configurable, evaluation-only pacing: default minimum start-to-start interval 20 seconds, configurable 0–60 seconds; maximum cumulative requested waits 300 seconds, configurable 0–300 seconds. Observed Retry-After can extend the next-case wait, but a gap above 60 seconds or exhausted wait budget prevents an early request. Explicit daily exhaustion prevents further provider calls. A completed case is not retried to improve its outcome. The original evaluator continues scoring unchanged decisions/expectations; pacing refusal appears as an evaluation error rather than fabricated success.

The default interval is a bounded workload-control choice, not an account-specific quota guarantee. Diagnostics from the next architect-authorized run must determine the actual constraint.

Future helper outputs use `STAGE_3B_*`, reject existing output destinations, collect safe attempt/validation diagnostics, and preserve the baseline CSV while copying a new evaluation CSV separately. The old Stage 3 JSON summaries, CSV, and notes remain byte-for-byte unchanged (SHA-256 checked). No new live result artifact was created during this task.

## Exact files changed during Stage 3B

Modified:

- `src/decision_validation.py`: scoped/named safety matching and sanitized rejection diagnostics.
- `src/llm_client.py`: safe rate metadata and smaller completion budget; bounded retries retained.
- `src/agents.py`: compact prompt without repeated tool evidence prose.
- `scripts/stage3_live_check.py`: bounded pacing, diagnostic collection, baseline/output protection.
- `tests/test_decision_validation.py`: safe/unsafe wording, clause/field separation, diagnostic safety, mandatory approvals.
- `tests/test_llm_client.py`: 429 window evidence/unknowns, sanitized metadata, diagnostic timeout and retry deadline, completion cap assertion.
- `tests/test_single_agent.py`: compact prompt preserves all material records, policy results, and references.

Created:

- `tests/test_evaluation_pacing.py`: pacing bounds, Retry-After, daily exhaustion, invalid configuration, existing output protection.
- `docs/STAGE_3B_OFFLINE_MEASUREMENTS.json`: mocked envelope byte measurements only.
- `docs/STAGE_3B_NOTES.md`: this handoff.

`src/solution.py` remains modified from Stage 3 but was not changed in Stage 3B. Stage 3 files were already untracked at task start; their current Git status must not be mistaken for new Stage 3B work.

## Verification

Commands included repository/status inspection, targeted reads/searches, official documentation lookup, baseline hash recording/checking, focused unittest discovery, mocked payload measurements, `.venv\Scripts\python.exe -m pip check`, bounded `verify_setup.py`, full `unittest discover -s tests -v`, and Git whitespace/scope checks.

- Focused suites: enforcement 14/14, client 21/21, pacing initially 5/5, single agent 12/12 passed. One additional output-protection test was added before full verification.
- Final full suite: **111 passed, 0 failed, 0 skipped**, 0.747 seconds.
- `pip check`: no broken requirements.
- Outside-sandbox preflight, bounded to 45 seconds: `PRE-FLIGHT PASSED`, exit 0.
- Sandbox preflight, bounded to 20 seconds: stalled at TestClient and ended with `subprocess.TimeoutExpired`. This is distinct from the successful local execution. Existing Starlette/httpx deprecation warning remains non-blocking.
- Baseline hashes: Stage 3 evaluation JSON, smoke JSON, notes, and `evals/results_single.csv` unchanged.
- Data, policy, public contract, public cases, and evaluator have no tracked diff. Whitespace checks passed.

The historical baseline is still **6/6 public minimum checks, 2/6 accepted AI recommendations, 3 rate-limited cases, 1 safety-rejected case**. No new live score is claimed.

## Readiness and limitations

Ready for an **architect-authorized controlled live evaluation**, not a claim that live reliability is fixed. That run should use the paced helper, capture sanitized window/predicate diagnostics, and compare both minimum checks and accepted AI outputs with the preserved baseline. It was not run here.

Remaining limitations: historical root causes cannot be reconstructed; safety matching is heuristic; the 1,200-token cap and pacing have only offline validation; provider account/organization limits remain unknown; synchronous I/O can overshoot a monotonic deadline until its phase timeout expires. Mandatory procurement controls and human authority remain enforced independently of model prose.

Stop here and await architect review; no Architecture B or later-stage work is authorized.
