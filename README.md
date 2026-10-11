# AI Procurement Request Copilot

A local decision-support product for employee software and service requests. It gathers employee, budget, catalog, purchase-history and vendor evidence, applies policy in Python, and uses Groq for advisory analysis. Supplied business records are synthetic.

**Selected architecture: B — staged Procurement Analyst and Policy/Risk Reviewer.** Architecture A, the single-agent baseline, remains available. Both share five tools and deterministic controls. The dashboard initially selects A; choose Staged for B. The public handler retains its `single` default for compatibility.

## Features and human control

- Request details, recommendations, evidence, approvals, missing information, risks and next steps.
- Deterministic budget, spending-tier, security, privacy, legal and assessment-expiry checks.
- Vendor risk over HTTP from the included FastAPI service, preserving conflicts and failures.
- Structured model output, grounded final decisions and explicit safe handoffs.
- Actual invocation telemetry and reproducible architecture comparisons.

The application cannot purchase, approve spend, accept terms or complete human reviews.

## Windows installation

Use **Python 3.12** (verified with 3.12.4), PowerShell, and internet access for package installation and Groq. The stack is Streamlit, FastAPI, Pydantic and `httpx`; no provider SDK or orchestration framework is required. Run subsequent commands from the repository root.

```powershell
git clone https://github.com/D4RK-ctrl/FDE-03.git
Set-Location FDE-03
python --version
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pip check
python verify_setup.py
```

Confirm Python is 3.12 before creating the environment. If activation is blocked, use `.\.venv\Scripts\python.exe` instead of `python` for subsequent commands. Preflight requires no Groq key and should report `PRE-FLIGHT PASSED`.

Create configuration without overwriting an existing file:

```powershell
if (!(Test-Path .env)) { Copy-Item .env.example .env }
```

Edit `.env` privately and set `GROQ_API_KEY` to your Groq key. The template is:

```ini
GROQ_API_KEY=
MODEL_NAME=openai/gpt-oss-120b
VENDOR_RISK_BASE_URL=http://127.0.0.1:8001
```

Keep the selected model. Never commit keys or include them in reports. `.env` is ignored by Git; operating-system environment variables take precedence.

## Start and use

```powershell
python run_local.py
```

The launcher uses the current interpreter to start the API at **http://127.0.0.1:8001** and Streamlit at **http://127.0.0.1:8501**. API health is at `/health`; vendor risk is at `/vendor-risk/{vendor_name}`. Start with ports 8001 and 8501 free; do not launch a duplicate API. Ctrl+C stops the launcher's child processes.

Select a request and architecture, inspect the details, then click **Analyze**. Choose Staged for the selected architecture B. Review evidence and required approvals before any human follow-up. Fallbacks are labeled as handoffs, not successful AI advice. Change selection or clear the result before another analysis. Analysis incurs Groq usage; selecting a request alone does not call the model.

## Architecture and safeguards

The shared adapter in [src/solution.py](src/solution.py) is:

```python
handle_request(request_id: str, architecture: Architecture = "single") -> ProcurementDecision
```

Each normal run executes `request_context`, `budget`, `catalog_history`, `vendor_security` and `policy_validation` once.

- **A / single:** one agent consumes evidence and policy, followed by final validation.
- **B / staged:** the analyst produces a structured draft; safety validation gates the reviewer. The reviewer receives the draft alongside original evidence and policy. Shared final enforcement validates the decision. An unsafe analyst draft stops the workflow.

Normal successful paths use one model request for A and two for B. Production permits at most one bounded transient retry per completion; controlled evaluation disables retries.

See the [diagram and assumptions](docs/ARCHITECTURE.md). [Procurement policy](data/procurement_policy.md) is authoritative and uses reference date **2026-09-30**. Money uses `Decimal`; available budget is annual budget minus committed spend, without deducting purchase history again. A budget pass grants no purchasing authorization. Deterministic approvals, risk flags, missing information, grounded evidence and human review survive model analysis.

Records and model drafts are untrusted data. Bounded prompts separate records from trusted instructions; models cannot execute tools or read files. Missing or conflicting evidence triggers clarification or review. Request evidence is sent to Groq during analysis, so this is not entirely offline. The local dashboard has no production authentication or deployment hardening.

## Automated checks

HTTP transports are mocked in unit tests; these checks require no Groq credentials or live calls:

```powershell
python -m pip check
python verify_setup.py
python -m unittest discover -s tests -v
git diff --check
```

Coverage includes joins, provenance, policy boundaries, sensitive access, assessment dates, vendor/provider errors, retry accounting, unsafe advice, enforcement, both workflows, UI state and comparison controls. Restricted Windows sandboxes can stall TestClient; distinguish that from local results. The user performs browser verification.

## Evaluation

The unchanged public runner supports both architectures:

```powershell
python evals/run_public_evals.py --architecture single
python evals/run_public_evals.py --architecture staged
```

These are live, potentially billable runs requiring the vendor API and key. They overwrite `evals/results_single.csv` or `evals/results_staged.csv`; preserve prior CSVs first. They use production retry behavior without comparison pacing. Passing minimum checks does not establish semantic quality or accepted AI advice.

After authorization and with sufficient quota, reproduce the controlled comparison while the launcher's API runs:

```powershell
python evals/compare_architectures.py --live --architecture both --max-attempts 18 --interval-seconds 60 --max-pacing-seconds 1200
```

Alternatively, start only the API in another terminal using `python -m uvicorn mock_api.app:app --host 127.0.0.1 --port 8001`. Do not duplicate the launcher's API. The comparison runs all six cases once per architecture, disables retries, enforces the attempt/pacing budgets, and creates timestamped JSON/Markdown reports under `docs/`. See [evaluation instructions](evals/README.md).

Final Stage 6D results: [JSON](docs/ARCHITECTURE_COMPARISON_20261011_132721_049614.json) and [Markdown](docs/ARCHITECTURE_COMPARISON_20261011_132721_049614.md).

| Metric | A: Single | B: Staged |
|---|---:|---:|
| Public minimum checks | 6/6 | 6/6 |
| Accepted AI outputs | 4/6 | 5/6 |
| Safe fallbacks | 2 | 1 |
| Groq HTTP attempts | 6 | 12 |
| Tool calls | 30 | 30 |
| Mean active latency | 1.890 s | 3.584 s |
| Total active latency | 11.337 s | 21.502 s |

All 12 decisions preserved mandatory human controls. The run used 18 attempts, 60 tools, **989.490 s pacing** and **1022.331 s wall time** (about 17 minutes). All provider responses were HTTP 200. Three safety rejections produced two A fallbacks (`unsafe_model_output`) and one B fallback (`reviewer_unsafe_model_output`).

The [decision memo](docs/ARCHITECTURE_DECISION.md) selects B for explicit responsibilities and a modest increase in accepted advice. A is faster and uses half the HTTP attempts; monetary cost was not measured. Six cases, nondeterminism and fixed evaluation order limit inference. Rejection predicates cannot determine unsafe wording versus false positives, and semantic quality has not been independently established. No statistically proven superiority is claimed.

## Repository and limitations

```text
app.py / run_local.py       Dashboard and local launcher
src/contracts.py           Public Pydantic contract
src/solution.py             Shared handler
src/tools.py / evidence.py  Five tools and internal evidence models
src/policy.py               Deterministic policy
src/agents.py               Architecture A
src/staged_agents.py        Architecture B
src/llm_client.py           Bounded Groq HTTP client
src/decision_validation.py  Grounding, enforcement and handoffs
src/data_access.py          Dataset access
src/vendor_client.py        Vendor-risk HTTP client
src/telemetry.py            Actual invocation counters
data/ / mock_api/           Synthetic records, policy and mock service
evals/ / tests/             Evaluators and offline regression tests
docs/ / resources/          Designs, historical reports and briefs
templates/                 Retained starter templates
```

See the [dataset dictionary](data/README.md), [original implementation plan](docs/IMPLEMENTATION_PLAN.md) and [submission readiness checklist](docs/SUBMISSION_READINESS.md). Stage notes and earlier results are historical checkpoints, not current installation instructions.

The vendor service is synthetic; category overlap does not prove functional equivalence or unused licenses. Language guards are heuristic. Live behavior depends on quota and model output; hidden-case performance and independent semantic quality are unverified. Human Security, Privacy, Legal, Finance and purchasing approvals remain outside this product.
