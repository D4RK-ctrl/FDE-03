# Stage 5 — Product UI

Branch: `codex/stage-5-product-ui`, created from Stage 4 commit `7994378`.
Scope: presentation and UI interaction only. No new dependencies, browser checks,
live provider requests, procurement rules, or comparative evaluations.

## Layout and workflow

`app.py` retains the existing Streamlit application and calls the unchanged
`handle_request(request_id, architecture=architecture)` adapter. The header states
that recommendations are advisory and never purchasing approval. The sidebar
selects a supplied request and either Single Agent — Architecture A (`single`) or
Staged Two-Agent — Architecture B (`staged`), and shows the fixed model name.

Request data is loaded through `src/data_access.py`. Request ID, product, vendor,
requester, department, annual cost, user count, justification, data access,
integrations and urgency appear before analysis. Missing values remain explicit;
zero cost differs from missing cost, and an empty integrations list means none
requested. Missing/ambiguous employee joins do not invent department information.
Display joins are not used to decide procurement policy.

Analyze Request invokes the pipeline with a spinner. The result presents the
actual recommendation and next step, AI accepted/fallback status, human-review
status, returned approval names as pending, missing information, visible risk
flags and separate human escalation. Evidence tables preserve source, finding
and reference and are grouped in expanders. Untrusted record text uses plain
text/table components; no unsafe HTML rendering is enabled.

Metrics show actual telemetry values, tool names, selected execution architecture
and elapsed `handle_request` time measured with a monotonic clock. Unknown counts
display “Not reported”; zero remains zero. Latency includes any pipeline transport
retries but excludes evaluation pacing. No approval or purchase action is offered.

## Session state and failures

Results, latency and sanitized errors belong to one `(request_id, architecture)`
selection. Rerenders retain that result without calling the pipeline. Either
selection change clears all previous output; changing back does not restore it.
Clear Results removes the result/error and permits another explicit analysis.
Analyze is disabled after completion or error until clearing/changing selection.
A running/completed guard also prevents duplicate execution on reruns. This guard
is session-local, not a global lock across users or separate browser sessions.

Provider fallback decisions are rendered as returned by the pipeline, with visible
AI-unavailable status and original risk identifiers. Unexpected display/pipeline
exceptions produce a fixed, actionable error with no raw exception text, provider
body, authorization header or stack trace. No replacement decision is fabricated.
The UI also refuses a result whose request identity mismatches the selection.
Operator-level cause diagnosis remains in existing pipeline diagnostics; the UI
deliberately does not display exception details.

`src/ui_presentation.py` centralizes request formatting, evidence grouping,
pending approval and human-review labels, telemetry labels, risk explanations
(including analyst/reviewer prefixes), and session-state operations. Unknown risk
flags retain their identifiers and receive a generic human-review explanation.

## Verification performed

- `.venv\Scripts\python.exe -m pip check`: PASS; no broken requirements.
- `.venv\Scripts\python.exe -m unittest tests.test_ui -v`: 16 passed locally,
  0 failed. Includes 10 helper checks and 6 headless Streamlit AppTest checks.
- `.venv\Scripts\python.exe -m unittest discover -s tests -v`: 161 passed,
  0 failed, 0 skipped, locally; reported test execution time 6.128 seconds.
- `.venv\Scripts\python.exe verify_setup.py`: PRE-FLIGHT PASSED locally;
  Python 3.12.4, imports, dataset integrity, contract/public-case validity and
  mock API checks passed. This validates case structure, not live evaluation scores.
- `.venv\Scripts\python.exe -c "import app,run_local; print('Application and launcher imports: PASS')"`:
  PASS, with no service launch or analysis call.
- `git diff --check`: PASS. Git noted normal LF-to-CRLF conversion for `app.py`.
- SHA-256 comparison of 57 pre-existing protected files: no changes, including
  source pipeline, datasets, policy, resources, tests, launcher and historical docs.

The sandbox headless UI invocation stalled and was interrupted (no assertion
failure result). Sandbox preflight was bounded at 20 seconds and raised
`subprocess.TimeoutExpired` during the FastAPI TestClient check. An earlier
Streamlit import diagnostic reported sandbox temp cleanup `PermissionError:
[WinError 5] Access is denied`. Bounded local execution resolved these environment
issues: UI/full suite used a 55-second limit, and preflight a 45-second limit.
Local tests emitted existing Starlette/httpx deprecation and bare-mode Streamlit
context warnings; no dependency changes were made to suppress them.

AppTest drives the application in process, without a browser. All UI pipeline
calls are mocked. Coverage includes both architecture routes, passive selection,
result retention, clearing, stale-result removal, duplicate-call prevention,
fallback visibility, actual metrics and sanitized errors. See the official
[AppTest documentation](https://docs.streamlit.io/develop/api-reference/app-testing/st.testing.v1.apptest).

## Startup and user verification

From the repository root, using the configured virtual environment:

```powershell
.\.venv\Scripts\Activate.ps1
python run_local.py
```

The unchanged launcher starts the vendor-risk API and Streamlit. The user may open
`http://127.0.0.1:8501`, inspect request details and both architecture labels, then
explicitly analyze a request when ready to make live provider calls. Check evidence
references, pending approvals, visible risks, next action and actual metrics.
Verify rerenders retain results, either selection change clears them, and Clear
Results enables a new analysis. Check narrow-window layout and keyboard navigation.

## Remaining limitations and review checkpoint

Browser appearance, mobile responsiveness, accessibility and full launcher/service
startup await user verification. Automated headless rendering and imports passed;
no browser or live Groq testing was performed. Streamlit 1.65.0 is the verified
local version; older versions allowed by the existing requirements range were
not tested for the current sizing API. No changes to requirements were made.

Files: modified `app.py`; created `src/ui_presentation.py`, `tests/test_ui.py`
and this document. No commits, staging, pushes, merges or PRs. No Stage 6 work.
Await architect review.
