# Stage 6 — Architecture evaluation framework (6C readiness corrections)

Stage 6C is offline only and adds no new live findings or architecture decision.
The completed Stage 6B pilot remains unchanged. Branch: `codex/stage-6-evaluation`, based on Stage 5 commit
`67e1806`. Production code, UI, public cases and historical results are preserved.

## Methodology and existing public checks

Run `evals/compare_architectures.py` after separate live authorization. The runner
uses the unchanged `src.solution.handle_request` pipeline and imports `evaluate`
from `evals/run_public_evals.py`; it does not invoke that runner's CSV-writing main
function or overwrite `evals/results_single.csv`.

Both architectures receive the same selected cases, in the original public-case
order. Runs are case-major: single then staged for each case. IDs select public
cases (`PUB-01` through `PUB-06`), not arbitrary request-specific behavior.
Unknown IDs and unsupported architecture selections are rejected.

Existing public checks evaluate approval/risk/missing-information groups, forbidden
risk substrings, maximum missing items, minimum evidence count and human review.
Those expectations remain unchanged. Passing them is a minimum-contract result,
not an accepted-AI or overall correctness score. Invalid output and execution
errors stay in the executed denominator and fail minimum checks.

## Deterministic evidence and safety checks

Evaluation-only subclasses capture the existing tools' policy result and evidence
from each actual run. They do not reload data, re-call the vendor API or recalculate
policy separately. This uses the shared engine as the ground truth for checking
agent enforcement; it is not an independent proof that the policy engine is correct.
Existing boundary tests cover the engine itself.

Checks verify request identity, human review, inclusion of all mandatory approvals,
policy flags and missing information, blockers/unresolved items in the human next
step, and consistency of telemetry against actual observed attempts and tools.
Every final evidence item's source, reference and finding must match the captured
canonical evidence exactly. Empty or invented evidence fails grounding.

The existing safety detector checks recommendation and next-step prose for unsafe
authorization/bypass claims. A clear detector result means no known pattern was
found; it is not a complete semantic pass. Semantic authorization, functional
suitability, recommendation quality and overall prompt-injection resistance are
explicitly marked `human_review_required`. Injection-control preservation checks
the applicable deterministic injection flag and absence of detected unsafe text.
Missing-data and provider-failure checks preserve the same controls; inapplicable
checks are marked accordingly rather than inflating pass counts.

AI acceptance is recorded only for schema-valid decisions with actual model HTTP
attempts and no `ai_analysis_unavailable` flag. Public passes, accepted AI,
fallbacks, errors and derived safety outcomes remain separate. A schema-valid
fallback can pass minimum checks and still counts as zero accepted AI outputs.

## Fairness assumptions

Hold model (`openai/gpt-oss-120b`), immutable case/data/policy snapshot, tool
implementation, provider configuration and retry settings constant. Both routes
execute all five shared tools normally. Hashes of captured evidence/policy detect
different external evidence between paired runs; a mismatch makes a pair less
comparable and needs investigation. Runs do not cache or fabricate HTTP evidence.

Fixed single-first order is reproducible but can introduce order/quota effects.
The selected case set is shared; an early stop is reported as partial, with planned
and executed denominators. Do not compare raw totals as though both architectures
finished the same coverage. Provider nondeterminism and the small six-case sample
limit any future conclusion. No winner is selected automatically.

Historical results remain historical: Stage 3 baseline had 6/6 minimum passes,
2/6 accepted AI outputs, three rate-limit failures and one safety rejection.
Stage 4B exercised only two staged cases. These are not a controlled full A/B
comparison. Stage 6B paired REQ-1001 and REQ-1005: four runs, six HTTP attempts,
20 tools, A accepted 2/2 and B accepted 1/2. The staged REQ-1005 reviewer returned
`reviewer_unsafe_model_output`; all final minimum and deterministic checks passed.
Pacing was 290.631 seconds. The original timestamped pilot JSON/Markdown are
preserved. Its exact rejection predicate cannot be recovered retroactively from
those artifacts, and no false-positive conclusion is made.

## Attempts, pacing and timing

The evaluator wraps HTTP transport without editing the Groq production client,
prompts or safety enforcement. Every transport invocation is counted, including
network/provider failures. Timeout/network/429/5xx errors become controlled
`LLMError` handoffs in this wrapper, preventing production retries for evaluation.
HTTP transport retries are zero. Other statuses and response parsing retain the
existing client validation, including structured output. HTTP 400 diagnostics
reuse the production allowlisted parser and consume streamed bodies once.
The evaluator also scopes the existing retry backoff ceiling to a negative value
while completing a call, so a read failure after headers cannot initiate a second
attempt. The original production value is restored after the completion; this
evaluation process must run separately from product traffic.

A shared attempt controller spans both architectures. It checks the budget before
each completion, including the staged reviewer. Exhaustion between stages produces
a policy-safe reviewer handoff without a second HTTP attempt. Global execution
then stops. Authentication/configuration failures and observed daily quota
exhaustion also stop pointless subsequent requests.

`ComparisonPacer` extends `EvaluationPacer` only in this runner for start-to-start spacing before every model call,
including analyst-to-reviewer. Pacing occurs before the production response
deadline begins. Interval is configurable from 0 to 60 seconds; cumulative requested
wait is configurable from 0 to 3,600 seconds (default 1,020). The original Stage 3
pacer retains its 300-second cap and production behavior is unchanged.
Provider Retry-After is respected if it fits
the per-wait and total limits; otherwise execution stops before an early request.
No completed case is retried. Rate-limit cause is recorded only where sanitized
headers/provider-reported windows establish it; unknown causes remain unknown.

Per-case wall latency is measured around the application call. Measured evaluator
pacing is subtracted to report active execution latency; transport/model/tool work
remains included. Root wall time includes runner overhead, excluding artifact
writing. Aggregates show total/mean active latency, pacing, actual attempt/tool
counts, unknown tool counts, provider/rejection diagnostics, fallbacks and check outcomes.
This is application time, not isolated inference time or a monetary cost estimate.

Normal full coverage: 12 runs, at most 18 Groq HTTP attempts (6 single + 12 staged)
and 60 tool invocations. With a 60-second interval, nominal maximum pacing is
17 × 60 = **1,020 seconds**. Both CLI and API validate this nominal capacity before
tools or HTTP execute: `(min(normal calls for selected scope, hard attempt budget)
- 1) × interval`, floored at zero. Insufficient budgets fail explicitly with the
required seconds; the requested interval is never shortened. This is a conservative
readiness requirement: active execution reduces actual waiting, but that duration
cannot be guaranteed before a run. A deliberately smaller hard attempt budget
supports a partial study with a correspondingly smaller nominal allowance.

Early analyst failures legitimately omit reviewer attempts. Measured counts/waits
then decrease; the evaluator does not send filler requests or retry cases to reach
18. Provider Retry-After may consume additional budget or trigger early termination.
The proposed final budget is 1,200 seconds, allowing 180 seconds beyond the nominal
estimate. The unchanged per-wait cap is 60 seconds; a longer Retry-After stops the
run rather than sending too early. Limits do not guarantee quota sufficiency. B normally
uses two calls and has different quota, cost and latency characteristics.

Successful 18-call spacing gives at least a **1,020-second first-to-last call-start
span**, plus the final response and initial tool/setup time: about **17–21 minutes**
under ordinary phase-timeout behavior. Actual sleeping has no guaranteed positive
minimum: work between starts consumes the interval (pilot-like short calls would
leave roughly 16–17 minutes of sleeping). Scheduling, provider delays and extra
quota waits may increase wall time; failures may end it early. This is an estimate,
not an enforced process deadline or measured full-study result.

## Reports and sanitization

Reports default to `docs/ARCHITECTURE_COMPARISON_<UTC timestamp with microseconds>.json`
and `.md`. Files use exclusive creation and never overwrite earlier artifacts.
The JSON includes configuration, selected IDs, completeness/stop reason, per-case
records, paired snapshot checks, diagnostic metadata and architecture aggregates.
Markdown presents paired rows, aggregate check counts, reasons and diagnostics.

The evaluator captures the validator's existing `decision_diagnostic` logging
events per request/architecture on the evaluation thread, restoring logger handlers
and level afterward. Only allowlisted conditions (`UNSAFE_RULES` names or
`credential_echo`) and fields (`explanation`, `next_step`, `concerns`) survive.
New `rejection_diagnostics` records contain request ID, architecture, agent stage,
global attempt number where associated, `attempt_association`, controlled failure
code, condition, field and `semantic_validity: undetermined`. Summaries count
rejection conditions; Markdown includes these events separately from HTTP errors.
No record message, arguments, sentence, prompt, full model output or credential is
retained. Unknown fields/conditions become controlled unclassified labels.

Stage attribution uses the trusted completion schema and active architecture;
attempt association is the most recent HTTP attempt within the serial run, labeled
`serial_run_last_attempt`. Production rejection events contain no intrinsic request,
stage or attempt identifiers. If no matching attempt occurred, attempt/stage remain
null and association is unavailable. Concurrent/reordered logging or externally
disabled logging cannot be correlated reliably; this evaluator must run in its own
serial process. A predicate does not prove the rejected sentence's meaning or that
the rejection was a false positive. Raw text is deliberately unavailable for such
semantic adjudication.

Only recommendation categories are saved; no full explanations, prompts, raw
business records, exception messages, authorization headers or unredacted provider
bodies are persisted. Approval names, risk identifiers and missing-information
items remain available with credential/authorization redaction and length bounds.
Controlled error codes and production allowlisted/numeric rate diagnostics are
used. Unknown errors are classified without copying their prose. Reports declare
`live`, `synthetic` or `unclassified` mode; synthetic/unclassified output is not live
evidence. Test-generated reports were confined to temporary directories.

## Proposed commands — only after architect live authorization

Start the existing mock API in a separate terminal, if not already running:

```powershell
.\.venv\Scripts\python.exe -m uvicorn mock_api.app:app --host 127.0.0.1 --port 8001
```

Full comparison, all six unchanged public cases:

```powershell
.\.venv\Scripts\python.exe evals/compare_architectures.py --live --architecture both --max-attempts 18 --interval-seconds 60 --max-pacing-seconds 1200
```

An architect-authorized subset, or one architecture only:

```powershell
.\.venv\Scripts\python.exe evals/compare_architectures.py --live --architecture both --case-ids PUB-01 PUB-02 --max-attempts 6 --interval-seconds 60 --max-pacing-seconds 300
.\.venv\Scripts\python.exe evals/compare_architectures.py --live --architecture single --max-attempts 6 --interval-seconds 60 --max-pacing-seconds 300
.\.venv\Scripts\python.exe evals/compare_architectures.py --live --architecture staged --max-attempts 12 --interval-seconds 60 --max-pacing-seconds 720
```

The CLI refuses execution without `--live`; that flag is an opt-in safeguard, not
architect approval. Secrets load through existing configuration and are never
printed. No live command above was executed during Stage 6C.

## Offline checks and limitations

Tests use mocked vendor HTTP responses and `httpx.MockTransport` through the real
application pipeline. They cover both routes/all six cases, unchanged scoring,
counts, fallback separation, invalid contracts, policy omissions, invented evidence,
authorization detection, pacing, partial budgets, quota/authentication stops,
timeout/400 diagnostics, credential redaction, exclusive artifact creation and
CLI live-opt-in protection. No LLM judge or additional dependency is introduced.

Required verification uses `.venv\Scripts\python.exe -m pip check`,
`verify_setup.py`, `-m unittest discover -s tests -v`, `git diff --check` and
`git status --short`. Preflight and full tests use bounded local subprocess
execution (45 and 55 seconds) because previous stages established Windows sandbox
TestClient/UI stalls; no new sandbox stall was induced for this task.

Stage 6A verified 179 offline tests with 0 failures. Stage 6C adds ten comparison
regressions, bringing that suite to 28 methods. The first focused run passed 26
and failed two new fixture predicate assertions: wording about Procurement approval
matched the earlier `purchase_authorization` rule rather than `completed_approval`.
The fixtures were corrected to isolate a completed Security review; the validator
was not changed. The corrected focused run passed 28/28. Final Stage 6C full suite
passed **189 tests, 0 failed, 0 skipped** (5.446 seconds). `pip check` found no
broken requirements; preflight returned `PRE-FLIGHT PASSED`; Git whitespace and
new-file syntax/whitespace checks passed. SHA-256 checks across 66 existing files
found changes only in the three authorized evaluation files, preserving the pilot
and all production/historical inputs. Existing Starlette/httpx deprecation and
bare-mode Streamlit context warnings were non-blocking. No live requests or real
pacing sleeps were performed during Stage 6C.

The framework patches adapter factories only within a synchronous evaluation
context. It is not intended to run concurrently with product requests in the same
Python process. Its current minimum-case format trusts the unchanged supplied
public cases. Pacing/quota constraints can yield partial evidence, and synchronous
HTTP phase timeouts are not a strict overall process deadline. Independent hidden
cases and human quality assessment remain future authorized work. No final decision
memo, live winner, UI change, final packaging or submission work is included.

Await architect review before live comparative evaluation.
