# Stage 4: staged two-agent architecture

Status: PASS for the authorized offline implementation scope. No live Architecture B
result, comparative quality improvement, or final shipping choice is claimed.

Branch: `codex/stage-4-staged-agent`, created from the latest Stage 3 branch commit
`94646a8af09c231bf417687632bc8555665265dc`. All changes remain local and uncommitted.

## Workflow and responsibilities

`handle_request(request_id, architecture="single")` retains Architecture A.
`handle_request(request_id, architecture="staged")` now runs Architecture B. The
public signature and `ProcurementDecision` output contract are unchanged. Invalid
architectures and invalid request IDs continue raising explicit errors.

Both paths create one telemetry counter, one EvidenceTools instance and one Groq
client. Request context, budget, catalog/history, vendor/security and deterministic
policy validation are each called once. Vendor-risk evidence still uses the existing
HTTP client; the staged path does not reread its fixture or fetch evidence again.

The Procurement Analyst interprets the business purpose, alternative candidates,
budget, vendor/security evidence and uncertainties. Its draft is validated before
any reviewer request: strict schema, caller request identity, canonical evidence
references, existing safety guard and credential-echo checks. Concerns receive
the same safety and credential checks. A failing draft stops execution.

The Policy/Risk Reviewer receives the original request, identical serialized shared
evidence and policy results, plus the validated draft under `untrusted_analyst_draft`.
It checks grounding, overlap, budget implications, missing information, approvals,
Security/Privacy/Legal escalation and unsafe authorization. Its strict advice passes
through the same final enforcement used by Architecture A.

Exactly two AI reasoning functions are introduced: `procurement_analyst` and
`policy_risk_reviewer`, in a small dedicated `src/staged_agents.py` module. Workflow
control and draft validation are deterministic Python, not additional agents.

## Schemas and trusted boundaries

`AnalystDraft` inherits strict `AgentAdvice` fields:
- request_id (1–200 characters)
- advisory recommendation enum
- explanation and next_step (each 1–1,200 characters)
- evidence_references (1–30 canonical references)
- required concerns list (0–10 entries, each 1–200 characters)

The reviewer returns existing strict `AgentAdvice`. Extra fields such as model-owned
required approvals or purchasing authorization are rejected. Both stages request
strict JSON schema output through the unchanged Groq client; local Pydantic bounds
remain enforced independently of the provider schema subset.

Separate compact system instructions define the analyst and reviewer responsibilities.
Request/catalog/history/vendor content and the analyst draft remain explicitly
untrusted user-message data. They cannot supply higher-priority instructions, tools,
file access or code execution. Both stages describe approvals as pending and must
not invent human sign-offs, unused licenses, functional equivalence or source records.
The reviewer can add advisory concerns in explanation/next_step, but cannot own or
remove mandatory final fields.

The shared `evidence_envelope` helper is extracted from the existing single-agent
function without changing Architecture A's system prompt or envelope representation.
Original evidence records and all deterministic policy results are preserved. The
reviewer receives one copy of that envelope plus the bounded draft.

## Shared authority and failure behavior

No policy calculation was duplicated or changed. Both architectures use September
30, 2026, the same spending thresholds, budget checks, assessment-expiry rules,
mandatory approvals, risk flags, missing information, blockers and unresolved checks.
Only application-collected evidence enters the final trail. Passing a model or budget
check does not authorize purchasing. Human review remains required.

Provider and schema failures return explicit handoffs with controlled stage-prefixed
codes, e.g. `analyst_timeout`, `reviewer_invalid_json`,
`analyst_unsupported_evidence_reference` or `reviewer_unsafe_model_output`.
HTTP 400 and 429 use the unchanged client's classifications and bounded retries.
The failing stage is visible without logging prompts, responses or credentials.
A reviewer failure never returns the analyst draft as a successful final response.
Unknown requests skip both stages and return the existing `request_unavailable`
handoff after the five tools. Vendor-service failures remain uncertainty in the
shared policy results and do not imply approval.

## Telemetry and comparison with Architecture A

Actual HTTP attempt counts come from the existing Groq client, and actual tool
counts come from EvidenceTools. Counts are not hardcoded into the final decision.
The unchanged public telemetry fields remain llm_calls, tool_calls and tool_names.
The adapter log includes architecture, actual counts, elapsed milliseconds and a
controlled failure code.

Observed mocked-path counts:

| Path | LLM attempts | Tools |
|---|---:|---:|
| Successful single | 1 | 5 |
| Successful staged | 2 | 5 |
| Analyst non-retryable failure | 1 | 5 |
| Reviewer non-retryable failure | 2 | 5 |
| Analyst repeated timeout/connection failure | 2 | 5 |
| Reviewer repeated timeout/connection failure | 3 | 5 |
| Unknown request or missing credential | 0 | 5 |

One vendor lookup occurs on a normal staged run, including a vendor-service outage.
The production client's two-attempt maximum remains per model stage; if both stages
recover after one transient retry, up to four HTTP attempts are possible. Its existing
48,000-byte input bound, 64,000-byte response bound, 1,200-token completion cap,
phase timeouts, 45-second soft budget per completion and maximum two-second backoff
were not increased. No product sleep/pacing or new provider calls were introduced.

## Files changed

Modified: `src/agents.py` (shared envelope extraction), `src/solution.py` (architecture
dispatch and architecture log label).

Created: `src/staged_agents.py`, `tests/test_staged_agent.py`, `docs/STAGE_4_NOTES.md`.

No existing tests were edited or weakened. No dependencies, public contract, policy,
datasets, evaluator/expectations, Groq client or safety-validator changes occurred.

## Actual verification

Commands: Git status/branch/log and source/document inspection; requested `git switch
-c codex/stage-4-staged-agent codex/stage-3-single-agent`; `.venv/Scripts/python.exe -m
pip check`; focused `-m unittest tests.test_staged_agent tests.test_single_agent -v`;
full `-m unittest discover -s tests -v`; bounded `verify_setup.py`; `git diff --check`;
Git diff/status; Python AST/whitespace checks; protected-file SHA-256 comparisons.

- First focused run: 36 tests, 35 passed, 1 error (`KeyError: 'CodePilot'`) in the new
  test's vendor-fixture lookup. The test used a product name instead of REQ-1002's
  vendor, BrandBoard. Only the test lookup was corrected; no source data changed.
- Final focused run: **39 passed, 0 failed, 0 skipped**, 2.073 seconds (25 new staged
  tests and all 14 existing single-agent tests).
- Final full local suite: **145 passed, 0 failed, 0 skipped**, 5.097 seconds (120
  pre-existing tests plus 25 new staged tests). Provider and vendor HTTP are mocked.
- Dependency check: **No broken requirements found**.
- Sandbox preflight: stalled at FastAPI TestClient and raised
  `subprocess.TimeoutExpired` after 20 seconds. This is not a passing result.
- Bounded local preflight outside the sandbox: **PRE-FLIGHT PASSED**, exit 0.
- Existing Starlette/httpx deprecation warning remains non-blocking.
- `git diff --check`, changed-file AST and whitespace checks passed. Git reported
  existing LF/CRLF conversion warnings, not whitespace errors.
- All **31 protected-file hashes** unchanged, including resources, historical
  Stage 3/3B/3C/3D/3E artifacts, data, public evaluations, contracts, shared tools,
  policy/evidence/telemetry and Groq client. Architecture A system prompt is
  byte-for-byte unchanged; a regression test verifies identical evidence packages
  and policy-owned final fields between A and B.

Tests cover strict stage schemas, missing fields, independent stage validation,
identity mismatch, hallucinated references, unsafe authorization and bypasses,
secret echo, draft-concern safety, provider timeout/connection/400/429 errors,
accurate retry counting, analyst failure preventing reviewer execution, reviewer
failure handoffs, missing requests/credentials, injection, budget shortfalls,
sensitive Security/Privacy data, expired/conflicting assessments, vendor outages,
canonical evidence, nonmutation and Architecture A regressions.

## Remaining limitations and checkpoint

Architecture B has only offline mocked validation. Real acceptance, latency,
provider structured-output behavior and quota impact remain unmeasured. Two
consecutive stages can exceed an 8K-token/minute account limit; compact envelopes
reduce duplication but do not guarantee quota compliance. A separately authorized
controlled live run needs an explicit total attempt budget and pacing strategy.

The shared prose guard remains conservative and heuristic. Canonical references
are verified, but no deterministic semantic proof covers every free-text claim.
Stage 3 safety rejection and historical HTTP 400 uncertainties remain recorded;
Stage 4 does not claim to fix them or to outperform Architecture A. Synchronous
I/O retains the existing soft-deadline limitation.

No live Groq calls, live evaluation, browser testing, UI changes, final architecture
decision memo, commits, staging, pushes, merges or PRs occurred. Do not begin Stage 5.
Stop here and await architect review and any separately authorized live test.
