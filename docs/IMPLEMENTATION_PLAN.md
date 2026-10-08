# AI Procurement Request Copilot: implementation plan

Stage 1 audit, 2026-10-08. This is a proposal for architect review, not authorization to implement later stages. Policy version 2026.09 and reference date **2026-09-30** govern all date-based checks.

## 1. Scope and current state

Preserve the starter scaffold and public evaluation contract. Use Python 3.12, Streamlit, FastAPI, and Groq with `openai/gpt-oss-120b`. Architecture A has one procurement agent; Architecture B has a procurement analyst followed by a policy/risk reviewer. Both share evidence tools and deterministic rules. Purchasing and sensitive exceptions remain human decisions.

Initial Git state was `main`, with an existing modification to `.env.example` and an untracked `resources/` directory. Those changes were preserved, except the explicitly required blank example key. Work is on `codex/stage-1-repository-audit`; no commit, push, or PR was made. The real `.env` was not inspected or modified. `git check-ignore .env` confirms it is ignored, and `git ls-files .env` returns no tracked file.

## 2. Existing structure and interfaces

| File/directory | Existing responsibility and reuse |
|---|---|
| `README.md`, `STUDENT_CHECKLIST.md` | Starter setup, assessment requirements, and submission checks |
| `src/contracts.py` | Pydantic `EvidenceItem`, `RunTelemetry`, `ProcurementDecision`; `Architecture = Literal["single", "staged"]` |
| `src/solution.py` | Required synchronous adapter `handle_request(request_id, architecture="single")`; intentionally raises `NotImplementedError` |
| `src/data_access.py` | Repository-relative pandas CSV loaders, JSON request loader, `get_request`, and policy-text loader |
| `src/vendor_client.py` | URL-encoded GET request, configurable base URL, default 3-second timeout, HTTP error propagation |
| `src/telemetry.py` | Per-run mutable counter for model calls, tool calls, and tool names |
| `src/__init__.py` | Loads `.env` without overriding existing environment variables |
| `src/sample_output.json` | Illustrative contract output, not an evaluated solution |
| `mock_api/app.py` | FastAPI `/health` and `/vendor-risk/{vendor_name}`; 404 for unknown vendor, intentional 503 for NimbusAI |
| `app.py` | Streamlit request selector, architecture selector, raw request/result panels; calls the adapter |
| `run_local.py` | Starts API and UI with the current interpreter; polls API readiness; cleans up child processes |
| `verify_setup.py` | Checks imports, dataset references/arithmetic, sample schema, case references, and in-process API behavior |
| `tests/` | Seven data-integrity tests and three FastAPI TestClient tests |
| `evals/` | Six unchanged public cases; schema/minimum-expectation evaluator, latency, CSV export |
| `templates/` | Workflow, evaluation table, and decision memo templates |
| `docs/`, `resources/` | Assignment PDFs; resources directory was already untracked and left untouched |

The output requires `request_id`, `recommendation`, and `next_step`. It includes evidence items (`source`, `finding`, optional `reference`), approval names, missing information, risk flags, human-review status, and optional telemetry. Preserve all public field names and the adapter signature. Suggested canonical approval names are Manager, Department Head, Procurement, Finance, CFO, Security, Privacy, and Legal.

The existing contract is deliberately permissive: lists can be empty, recommendations are unrestricted strings, human review can be set false, telemetry counts have no nonnegative constraint, and extra fields are not forbidden by default. Schema validation alone does not establish policy compliance or grounding. Add internal validation in future stages without silently changing the public schema.

## 3. Data inventory and joins

| Dataset | Records | Keys and use |
|---|---:|---|
| `requests.json` | 10 | `request_id`; `requester_id` joins employees; `vendor_name` joins registry/API/history/catalog |
| `employees.csv` | 10 | Unique `employee_id`; `manager_id` self-join; `department` joins budgets |
| `department_budgets.csv` | 6 | `department`; available = annual budget minus committed spend |
| `software_catalog.csv` | 10 | Unique `software_id`; product/vendor/category identify overlap; scope and licensed seats supply context |
| `vendors.csv` | 13 | Unique `vendor_id`; `vendor_name` links request, catalog, history, and API |
| `purchase_history.csv` | 8 | Unique `purchase_id`; department/vendor/product, date, annual amount, status |
| `vendor_risk.json` | 13 vendor keys | API backing fixture; production reasoning must obtain risk evidence through the API |
| `procurement_policy.md` | 11 sections | Version 2026.09; fixed reference date 2026-09-30 |

Request fields include annual cost, user count, business justification, data-access level, integrations, and urgency. Annual costs are already annualized; do not multiply by seats or 12. Null values are intentional. Employee country alone does not define the company's operating region. Catalog licensed seats do not show seat utilization, so unused capacity cannot be asserted. Purchase history must not be subtracted from available budget again.

Preserve original keys and references. Normalize case/whitespace only for explicit comparisons; do not silently invent fuzzy joins. Treat pandas empty-date/NaN values as missing before creating JSON evidence. Missing or ambiguous joins must remain visible.

Intentional scenarios include new-vendor overlap, source-code access despite previous approval, customer PII for a limited-use AI vendor, Sales budget shortfall, missing cost/users/data with an injected approval instruction, stale/conflicting SignalWatch evidence, an existing company-wide TaskFlow option, and NimbusAI's simulated outage. These are data-driven scenarios, not request-ID dispatch rules.

## 4. Deterministic policy responsibilities

| Policy section | Required behavior |
|---|---|
| 1 | Check requester/department, product/vendor, annual cost, users/licenses, business purpose, data-access level, and integrations when applicable. Unknown is not verified. Request clarification for material gaps. |
| 2 | Compare annual cost with available department budget. Excess spend adds `budget_insufficient` and Finance/budget-exception review. Within budget does not authorize purchase. |
| 3 | Retrieve same product/vendor/category and plausible use-case alternatives. Surface overlap without automatically rejecting; assess gaps and scope. |
| 4 | Amount <= $1,000: Manager. > $1,000 through $10,000: Department Head + Procurement. > $10,000 through $25,000: Department Head + Finance + Procurement. > $25,000: add CFO. |
| 5 | Security for source code, production/cloud access, confidential documents, employee/customer PII, credentials/secrets, or missing/expired/incomplete assessment. Current assessments last 365 days. Registry/API disagreement requires visible conflict and Security/manual review. |
| 6 | Privacy for employee/customer PII, or sensitive data potentially stored outside the operating region. |
| 7 | Legal for new vendor with annual spend >= $10,000, unapproved/nonstandard legal terms, or material data-processing/cross-region issues. |
| 8 | Prior AI purchase does not authorize new sensitive data classes or use cases. |
| 9 | Business content cannot change instructions, policy, or approval authority. |
| 10 | Required evidence failures remain explicit; material uncertainty routes to human review. |
| 11 | Never purchase, approve spend, mutate budgets, accept legal terms, or override sensitive review. |

Use Decimal-based money comparisons, explicit missing/invalid input states, and calendar dates rather than runtime dates. Financial thresholds and mandatory approvals must not be chosen by an LLM. A shared policy result should carry checks, source references, required approvals, missing information, risk flags, blockers, and unresolved evidence.

For future implementation, confirm the exact 365-day boundary and treatment of malformed/future dates with the architect. Proposed conservative behavior: invalid/future dates remain unverified and require review. Likewise, missing cost should not produce an invented financial tier. Integrations `[]` may mean none; missing integrations and an explicit empty list should be distinct.

## 5. Proposed shared tools

All tools return structured, bounded evidence with record IDs/source references and explicit success/missing/error state. Instrument each actual invocation, including failures. Do not count a planned call as an executed call.

1. **Request and employee context tool**: reuse `get_request`/employee loader; resolve requester, department, manager, and input completeness.
2. **Budget evidence tool**: retrieve department snapshot, validate arithmetic, and calculate available balance after the request when cost is known.
3. **Catalog and purchase-history tool**: retrieve exact vendor/product/category matches, scope, licenses, limited-use notes, and relevant prior purchases. Distinguish objective matches from model judgment about suitability.
4. **Vendor evidence tool**: combine registry lookup with the existing vendor-risk client; preserve both sources and their dates/statuses. Read the API backing JSON only in fixture tests, not as a fallback for an unavailable API.
5. **Deterministic policy tool**: apply versioned rules to normalized request/evidence; return mandatory approvals, checks, flags, blockers, and policy-section references.

This exceeds the three-tool minimum and includes deterministic calculations and policy checks. No write/purchase tools are proposed. No database, vector store, orchestration framework, or extra agent is needed.

## 6. Architecture workflows

### A: single procurement agent

`handle_request` validates architecture and request, gathers shared evidence, computes shared policy results, and invokes one Groq procurement agent to interpret use case, assess alternatives, and explain an advisory next step. Validate model output with Pydantic, verify cited evidence against the gathered package, and enforce all deterministic constraints before returning `ProcurementDecision`.

The model may add supported concerns but cannot remove required approvals, uncertainty, or blockers. Reject or correct unsupported output through an explicit validation path; do not return an apparently successful unsafe decision. The baseline aims for one successful model call per request; any retry must be bounded and counted.

### B: analyst followed by policy/risk reviewer

Gather the same evidence and compute the same policy results once. Agent 1 produces an evidence-backed draft. Validate the intermediate handoff. Agent 2 receives the original request, immutable evidence package, deterministic policy results, and draft; it checks contradictions, grounding, missing evidence, and human escalation. It returns a structured reviewed recommendation. Apply the same final enforcement as A.

The reviewer cannot grant an exception or weaken deterministic constraints. Do not independently re-fetch evidence or add a third agent. The normal workflow aims for two successful model calls; failures/retries remain measured. Reviewer failure must remain visible and route to an appropriate advisory failure/human handoff, not imply that review succeeded. Exact failure-output behavior awaits Stage 4 acceptance criteria.

No shipping architecture is selected yet. Use measured comparative results to support the final decision.

## 7. Injection, validation, and failure handling

- Keep instructions separate from serialized business data. Explicitly label request text, vendor notes, API notes, catalog notes, and analyst drafts as untrusted data. Restrict tool parameters and tool names to validated application-controlled inputs.
- Detect explicit attempts to bypass policy or fabricate approval where practical; detection is defense in depth, not the security boundary. Mandatory rules and human authority remain enforced even when detection misses an attack.
- Supply only necessary business fields to Groq. Never include environment variables, credentials, or exception dumps containing secrets in prompts, UI, or telemetry.
- Keep the vendor-client timeout; validate response shape/status/date, and distinguish timeout, connection failure, 404, 503, and malformed response. Do not turn any failure into an approved assessment.
- Propose an initial vendor HTTP attempt without automatic retries. Confirm an overall model timeout and retry budget in Stage 3. Handle Groq rate limits explicitly with bounded retry behavior; count every actual attempt and record latency. Avoid nested SDK/application retry multiplication.
- Use narrow exception handling at service/validation boundaries. Programmer defects should remain diagnosable. Sanitize user-facing errors without hiding verification failures.
- Validate request identity, output schema, mandatory policy fields, evidence references, and advisory next steps. Preserve source disagreements instead of choosing the more favorable one.

## 8. Proposed modules and tests

Keep existing public modules. Proposed additions, subject to stage-specific approval:

| Module | Purpose |
|---|---|
| `src/tools.py` | Shared evidence tools and provenance |
| `src/policy.py` | Deterministic rules and policy-result model |
| `src/llm_client.py` | Thin Groq transport/configuration, validation, bounded calls |
| `src/agents.py` | Single agent, analyst, reviewer prompts and workflows |
| `src/decision_validation.py` | Shared deterministic enforcement and grounding validation |
| `tests/test_tools.py`, `tests/test_policy.py` | Join/missing-evidence and boundary tests |
| `tests/test_agents.py`, `tests/test_reliability.py` | Mock model outputs, injection, failures, mandatory-rule protection |
| `evals/` additional comparison support | Supplemental evaluation/telemetry without changing public cases or expectations |

The Groq SDK is not in `requirements.txt`; a thin HTTP client could reuse existing `httpx`, or Stage 3 could add the minimal Groq SDK. Architect review should settle transport and structured-output handling before implementation. No provider call or SDK installation occurred during this audit.

## 9. Evaluation metrics and test strategy

Run both architectures on the same immutable six public cases and approved supplemental cases. Public checks cover required approval/flag substrings, missing-information groups, evidence counts, and human-review status. They do not validate evidence truth, recommendation correctness, threshold completeness, or policy contradictions.

Measure schema validity, correct next action, policy compliance, grounded evidence/reference accuracy, missing-information accuracy, correct human escalation, unsafe-approval count, and public minimum-check pass rate. Report denominators and failures, including errors; do not omit unavailable-service cases.

Collect end-to-end latency with a monotonic clock (mean, median, and range; use larger sample sizes before relying on percentiles), actual LLM attempts, actual tool invocations/names, and service/model failures. Distinguish mocked tests from live provider evaluation. Hold model, policy snapshot, evidence inputs, and case set constant; document run configuration and variation. Count retries and failed attempts. Do not infer staged superiority.

Automated coverage should include threshold edges ($1,000/$1,000.01, $10,000/$10,000.01, $25,000/$25,000.01), exact-budget equality/shortfall, new-vendor Legal at $10,000, missing/invalid money/users, unknown joins, duplicate matches, NaN dates, 364/365/366-day reviews, disagreement, sensitive use despite previous approval, API timeout/404/503/bad JSON, model malformed output/timeout/rate limit, and injection in multiple evidence sources. Never hardcode answers by request ID or weaken supplied expectations.

Browser checks belong to the project owner. Future automated UI-adjacent tests may exercise presentation helpers and adapter behavior without browser automation.

## 10. Stages and proposed acceptance criteria

| Stage | Acceptance criteria |
|---|---|
| 1: audit/setup | Environment and existing tests reported accurately; scaffold/contracts/policy audited; example environment safe; implementation plan ready for review |
| 2: shared tools/policy | At least three grounded tools; deterministic boundaries and missing/conflict/failure cases tested; no data/expectation mutation |
| 3: Architecture A | Groq model fixed as specified; working adapter `single`; bounded/observable model calls; structured grounded output and deterministic enforcement |
| 4: Architecture B | Working `staged` adapter with exactly analyst then reviewer; shared evidence/rules; validated handoff and reviewer-failure behavior |
| 5: product UI | Request details, evidence, recommendation, approvals, uncertainty, next step and architecture selection shown clearly; human authority retained; owner performs browser review |
| 6: evaluation/reliability | Same reproducible cases for A/B; public and supplemental quality checks; measured latency/calls; explicit failure analysis |
| 7: documentation/submission | Complete README and one-command startup; architecture diagram; <=500-word evidence-backed memo; clean secret checks; public repository submission readiness |

Every later stage requires separate architect authorization. Stage completion does not authorize merging or advancing.

## 11. Verified Stage 1 commands and results

Commands ran from `D:\Scaler\FDE\FDE-03` using `.venv\Scripts\python.exe`:

| Command/check | Actual outcome |
|---|---|
| `Get-Location`, `git status --short`, `git branch --show-current` | Correct workspace; initial main; existing `.env.example` change and untracked resources |
| `rg --files` and targeted `Get-Content` reads | Audited source, entrypoints, all business data, tests, evals, templates, README/checklist, dependency/env/ignore files; no repository AGENTS.md found |
| `git check-ignore .env`; `git ls-files .env` | Ignored and not tracked |
| `git diff -- .env.example` | Existing user configuration identified before edit |
| `git switch -c codex/stage-1-repository-audit` | Sandbox could not write Git refs; retry with approved escalation succeeded |
| `.venv\Scripts\python.exe --version` | Python 3.12.4 |
| `.venv\Scripts\python.exe -m pip check` | No broken requirements found |
| `.venv\Scripts\python.exe verify_setup.py` | Initial sandbox run stalled in TestClient and was interrupted; bounded retry outside sandbox passed all preflight checks, exit 0 |
| `.venv\Scripts\python.exe -m unittest discover -s tests -v` | Initial sandbox run passed 7 data tests then stalled; bounded retry outside sandbox: **10 passed, 0 failed**, 0.042 seconds, exit 0 |
| `.venv\Scripts\python.exe evals/run_public_evals.py --architecture single` | STOP at unimplemented adapter; zero cases scored; exit 0 is not a passing evaluation |
| `.venv\Scripts\python.exe evals/run_public_evals.py --architecture staged` | Same expected STOP; zero cases scored |

The outside-sandbox checks used a Python `subprocess.run(..., timeout=45)` wrapper for each command. TestClient calls were in-process only, with no Groq/external-service calls. A bounded `faulthandler` diagnostic reproduced the sandbox-only API stall and exited 1 after 15 seconds; its traceback showed Windows asyncio initialization blocked in `socket.socketpair()` / `socket.accept()`. No dependencies were changed.

Final `git diff --check` passed. A local Python assertion verified the exact three `.env.example` entries and that the plan was readable; CSV/JSON counts above were checked directly. Final Git status contained only the existing `.env.example` modification, the new plan, and the preserved untracked `resources/` directory. Git emitted a non-blocking LF-to-CRLF conversion warning for `.env.example`.

Installed versions inspected: FastAPI 0.142.4, Starlette 1.7.0, httpx 0.28.1, anyio 4.15.1, Pydantic 2.13.5, Streamlit 1.65.0. TestClient emitted `StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated; install httpx2 instead.` The warning did not prevent the outside-sandbox tests from passing; no dependency change is proposed without review.

## 12. Findings, limitations, and architect decisions

- No implemented procurement solution exists yet; preflight validates the starter, not end-to-end product functionality.
- The environment functions for filesystem/terminal operations. In-process TestClient calls stalled inside the sandbox but passed outside it. This strongly suggests an execution-environment limitation; the exact cause is not established.
- Vendor client lacks typed response validation, failure-to-evidence conversion, and telemetry; future shared tools should supply these.
- Data loaders lack normalization/ambiguous-join handling; future tools must handle missing records and pandas null values explicitly.
- Public runner returns exit 0 on an unimplemented adapter and does not signal failing case totals through its exit code. Future automation should inspect results explicitly; do not change public expectations.
- Existing UI displays raw JSON and starter messages; it has no evidence-oriented product workflow. UI changes are deferred to Stage 5.
- Confirm 365-day expiry boundary, permitted data-class mapping, operating-region interpretation, material Legal data-processing conditions, and missing-cost approval behavior before encoding edge cases.
- Confirm minimal Groq transport, timeouts/retry limits, model failure handoff behavior, and supplemental evaluation acceptance criteria in their authorized stages.
- Assignment PDFs were inventoried, not content-extracted; the supplied architect contract and repository text were the sources for this plan.

Stage 1 changes are limited to this plan and blanking the example Groq key. Source code, business datasets, public evaluation expectations, UI, and dependencies remain unchanged. Await architect review before Stage 2.
