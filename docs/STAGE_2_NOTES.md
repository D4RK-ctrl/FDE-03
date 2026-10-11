# Stage 2: shared tools and deterministic policy

Implementation is local on `codex/stage-2-tools-policy`, created from the latest Stage 1 branch. No agents, live provider integration, UI changes, datasets, public expectations, or public contracts were changed. The Stage 1 work and `resources/` were preserved. No commits or pushes were made.

## Reusable interfaces

Create one `EvidenceTools(telemetry=None, snapshot=None)` per run. `snapshot` is an optional injected dictionary of dataset records for isolated tests; normal use loads repository data through existing helpers. Loaded data is cached within that run and pandas nulls become `None`. The returned request is copied without rewriting its business content.

| Method | Input | Output |
|---|---|---|
| `request_context(request_id)` | Request ID | `RequestContext`: original request, employee, manager, department, missing fields, join issues, evidence |
| `budget(context)` | Request context | `BudgetEvidence`: Decimal annual/committed/available/requested/remaining amounts; sufficient is true, false, or unknown |
| `catalog_history(context)` | Request context | `CatalogEvidence`: candidates with match reasons, scope/seats, history, possible overlap, truncation state |
| `vendor_security(context)` | Request context | `VendorEvidence`: independent registry/API records, statuses/dates, conflicts, explicit API error, evidence |
| `policy_validation(context, budget, catalog, vendor)` | Four evidence results | `PolicyResult`: approvals, missing information, flags, blockers, unresolved conditions, policy references, human review |

`src/policy.py::validate_policy(...)` is also directly callable without tool telemetry. `policy_validation` records its invocation exactly once. Other tool methods likewise count once, including failed calls; model-call counts remain zero. The low-level HTTP request is part of the vendor tool, not a second tool invocation. No retries are performed.

## Rules and error behavior

- Spending tiers use Decimal and exact inclusive boundaries. Missing/invalid costs never receive an invented tier; zero cost is valid. Amounts with more than two fractional digits are rejected rather than rounded. Negative, boolean and nonfinite amounts are invalid.
- Available budget is annual budget minus committed spend. Purchase history is not subtracted. Snapshot inconsistencies remain unverified. Unknown budget is not labeled insufficient or sufficient.
- Security assessments use 2026-09-30. Ages 364 and 365 days are valid when status is approved; 366 days is expired. Missing, invalid and future dates require review. Conflicts preserve both sources.
- Sensitive data classes and explicit source-code/production/cloud-account/credentials integrations trigger Security regardless of prior approval. PII triggers Privacy. Sensitive cross-region evidence triggers Privacy and Legal.
- New-vendor Legal starts at $10,000 inclusive; unapproved terms require Legal. Missing newness/cost/terms remain unresolved. PII alone does not fabricate a material Legal issue: Privacy/Legal confirmation remains an explicit unresolved condition when the API does not establish such an issue.
- Candidate matching uses exact normalized product/vendor/category and explicit catalog-product mentions in purpose. It never asserts functional equivalence or unused seats. Catalog scope, license counts and history remain visible for later reasoning.
- Request text and all notes remain data. Optional injection pattern detection adds a flag but does not control approvals. Deterministic checks do not execute business content or allow approval exemptions.
- The vendor tool calls the existing HTTP client with its default 3-second timeout; it never reads the risk fixture as fallback. Errors include `timeout`, `connection_error`, `http_404`, `http_503`, `malformed_response`, identity mismatch, and other explicit transport errors. Missing API evidence adds Security review and blockers.
- Local file/parse failures yield explicit error states. No blanket exception handler masks programmer errors. Missing/ambiguous joins are unverified, including empty join keys.
- Results are bounded: at most 25 catalog and 25 history records; truncation is explicit and policy-blocking. Dataset input is limited to 10,000 rows and 32,000 serialized characters per row; oversized records are rejected, not silently rewritten. Typed API fields include length limits and strict booleans. The original oversized request stays in its source file; an error result does not copy a truncated version.
- Outputs are advisory. `PolicyResult.human_review_required` is always true; there is no purchasing/approval capability.

## Verification and limitations

The new tests mock `src.vendor_client.requests.get`; they require no service, network, or Groq key. Existing FastAPI tests use their unchanged in-process TestClient. Both architectures and the public adapter remain unimplemented, so these results do not demonstrate end-to-end agent quality.

Known boundaries for architect review: data-class recognition is explicit; unknown classes require clarification. Candidate retrieval is lexical, with functional suitability deferred to future reasoning/human review. Injection detection is heuristic and optional; rule enforcement is the actual boundary. Non-cross-region material data-processing issues require human clarification instead of parsing free-form notes as authoritative policy. Internal evidence models are mutable Python objects and should remain application-controlled in future orchestration.

Commands executed include Git status/branch checks and branch creation, source/data inspection, focused unittest discovery, `pip check`, bounded `verify_setup.py`, full unittest discovery, and diff/scope checks. The sandbox preflight passed imports/data/contracts but timed out in TestClient after 20 seconds with `subprocess.TimeoutExpired`. The same preflight outside the sandbox passed within a 45-second bound. A non-blocking Starlette TestClient/httpx deprecation warning remains; dependencies were not changed.

Final verification: `pip check` reported no broken requirements; preflight reported `PRE-FLIGHT PASSED`; full unittest discovery reported **58 passed, 0 failed** (10 existing, 22 tool tests, 26 policy tests), in 0.564 seconds. Focused tests had also passed before final review refinements. AST parsing and whitespace checks passed, including `git diff --no-index --check` for new untracked files. Final status has six new untracked files only; no existing tracked file was modified during Stage 2.
