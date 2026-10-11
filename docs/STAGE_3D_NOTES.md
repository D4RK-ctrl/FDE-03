# Stage 3D: safety and provider-error corrections

Status: PARTIAL. Targeted corrections and offline verification are complete on
`codex/stage-3-single-agent`. Historical live failure causes remain only partially
diagnosable, and the corrected behavior has not been verified live.

## Safety diagnosis and correction

Stage 3C preserved `completed_approval` in `next_step` for REQ-1002 and REQ-1005,
but did not preserve the wording. We cannot identify the exact matching substring
or establish whether those two actual responses were safe or unsafe.

Confirmed defects reproduced before editing: `Ensure Security review is completed`
and `Do not purchase until the Security review is completed` were both rejected.
The first is an instruction to obtain/verify review, not an assertion of completion.
The second was missed by the future-prerequisite mask because it did not accept
the article `the`. Separately, `Security has signed off` and `Finance approval has
been granted` incorrectly passed the old matcher.

The correction uses reusable review-subject/status patterns. Future prerequisites
accept articles and required-review modifiers. A narrowly anchored imperative
(ensure, confirm, verify, make sure) masks only its review-status object. The rest
of the clause and every other clause/field remain checked. Fabricated active
sign-offs, named reviewer authorizations, and passive granted/received/approved
statuses are rejected. There is no request-ID-specific branch or exact-sentence
whitelist. Existing negative purchasing handling remains in place.

Trusted instructions now explicitly identify all required approvals as pending,
state that this evidence package contains no completed purchasing-approval records,
and distinguish vendor assessments from purchase authorization. Unsafe claims
still cause a schema-valid, explicit human handoff.

## HTTP 400 diagnosis and correction

Stage 3C retained HTTP 400, input size 8,139 bytes, completion cap 1,200, and quota
headers. It retained no provider error code, type, parameter, or message. Those
facts cannot distinguish schema/request formatting, context/output limits,
unsupported parameters, provider generation validation, or another issue.

The existing request uses the documented endpoint, strict JSON schema, all fields
required, `additionalProperties: false`, non-streaming output, the supported GPT-OSS
model, `reasoning_effort=low`, and `max_completion_tokens`. Five other cases using
the same configuration returned HTTP 200. No universal configuration defect was
established, and the schema/payload parameters were not changed speculatively.

Primary documentation reviewed:
- [Structured outputs](https://console.groq.com/docs/structured-outputs)
- [Chat-completion API reference](https://console.groq.com/docs/api-reference)
- [API error responses](https://console.groq.com/docs/errors)

HTTP 400 now has optional troubleshooting diagnostics bounded to 4,096 bytes and
the existing deadline. Only exact allowlisted error codes/types/parameter names
survive; messages, failed generation, arbitrary headers, and arbitrary provider
strings are excluded. Recognized codes produce explicit controlled handoff codes:
`provider_output_validation_failed`, `context_limit_exceeded`,
`provider_schema_rejected`, or `unsupported_parameter`. These describe a provider
report, not proof of a local root cause. Unknown, malformed, oversized, or unreadable
bodies retain `request_rejected`. These code examples are mocked coverage, not
reconstructed Stage 3C provider metadata. HTTP 400 is never retried.

Strict provider output mode and local Pydantic validation remain enabled. Model,
48,000-byte input bound, 64,000-byte success-response bound, 1,200-token completion
cap, timeouts, total deadline, two-attempt maximum and two-second maximum backoff
remain unchanged. Normal successful runs still use five tools and one model call.

## Files changed in this task

Modified existing files (already untracked at task start):
- `src/decision_validation.py`
- `src/agents.py`
- `src/llm_client.py`
- `tests/test_decision_validation.py`
- `tests/test_llm_client.py`
- `tests/test_single_agent.py`

Created: `docs/STAGE_3D_NOTES.md`.

`src/solution.py` remains modified from Stage 3 and was not edited here. Existing
user work, including `resources/`, was preserved.

## Verification and actual outcomes

Commands included Git status/branch/diff inspection, source/test/diagnostic reads,
offline safety reproductions, focused unittest invocation, `.venv/Scripts/python.exe
-m pip check`, full `-m unittest discover -s tests -v`, bounded `verify_setup.py`,
and protected-file SHA-256 comparisons. No live evaluation command was run.

- Initial focused run: 42 tests, 41 passed, 1 failed. A newly added test accumulated
  four HTTP attempts but incorrectly asserted one after the loop. The test now
  resets its counter for each case and asserts one attempt per mocked response.
  Existing retry-deadline assertions were restored to their original test.
- Sandbox full run: incomplete; stopped at FastAPI TestClient, with an existing
  temporary-directory test error. A focused sandbox reproduction produced
  `PermissionError: [WinError 5] Access is denied` under the sandbox's temp directory
  (1 test, 1 error). No test expectations were weakened to hide this restriction.
- Sandbox preflight: `subprocess.TimeoutExpired` after 20 seconds at TestClient.
- Bounded local full suite: 120 passed, 0 failed, 0 skipped, initially 2.345 seconds.
- Final local full suite after adding further unsafe semantic variations: **120
  passed, 0 failed, 0 skipped**, 4.905 seconds.
- Bounded local preflight: **PRE-FLIGHT PASSED**, exit 0.
- Dependency check: **No broken requirements found**.
- `git diff --check`: passed; existing LF/CRLF warning for `src/solution.py`.
  Whitespace checks separately covered the six changed untracked source/test files.
- SHA-256 verification: all 20 protected files unchanged, including all data files,
  policy, public contract, deterministic policy engine, evaluator/expectations,
  baseline CSV, and all existing Stage 3/3B/3C notes/results.

Regression coverage includes imperative approval requests, future/negated purchasing
conditions, fabricated completed approvals, mixed safe/unsafe clauses, hallucinated
references, mandatory approvals/missing information/blockers/human control, malformed
provider responses, HTTP 400 known and unknown codes, diagnostic body bounds/timeouts,
secret exclusion, local schema constraints, unchanged retry accounting, and a complete
five-tool adapter handoff for rejected provider output. Nine test methods were added.

## Limits and next checkpoint

Deterministic approvals, budget/security/privacy/legal rules, grounded evidence and
human authority remain unchanged. Language validation is still conservative and
heuristic, not a complete semantic proof. Unknown/new provider codes intentionally
remain unclassified. Synchronous reads remain subject to their phase timeout.
No claim is made that the historical HTTP 400 is fixed or that the live rejection
rate has improved.

A final controlled live verification is justified after architect review and separate
authorization, to test the corrected language handling and collect safe provider
metadata if another 400 occurs. No Groq requests were made here. No commits, staging,
pushes, merges, PRs, UI changes, dataset/evaluation changes, Architecture B, or Stage 4
work occurred. Stop and await architect review.
