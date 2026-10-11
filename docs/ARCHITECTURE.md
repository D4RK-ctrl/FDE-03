# Architecture and assumptions

Architecture B is selected for shipping; both implementations remain available. The public handler and dashboard initial selection retain Architecture A for compatibility.

```mermaid
flowchart TD
    EMP["Employee and request datasets"] --> UI["Streamlit dashboard: app.py"]
    USER["User selects request and architecture"] --> UI
    UI --> HANDLER["src.solution.handle_request"]
    EVAL["Public and comparison evaluators"] --> HANDLER
    HANDLER --> TOOLS["One EvidenceTools instance per run"]
    subgraph SHARED["Shared evidence and deterministic policy"]
        CONTEXT["request_context: employee, department, manager"]
        BUDGET["budget: Decimal available funds"]
        CATALOG["catalog_history: matches and possible overlap"]
        VENDOR["vendor_security: registry and HTTP risk evidence"]
        POLICY["policy_validation: src.policy.validate_policy"]
        DATA["src.data_access: budgets, catalog, vendors, history"]
        RULES["data/procurement_policy.md"]
    end
    TOOLS --> CONTEXT
    CONTEXT --> BUDGET
    BUDGET --> CATALOG
    CATALOG --> VENDOR
    VENDOR --> POLICY
    EMP --> CONTEXT
    DATA --> BUDGET
    DATA --> CATALOG
    DATA --> VENDOR
    RULES --> POLICY
    VENDOR --> API["FastAPI mock_api.app: localhost:8001"]
    FIXTURE["data/vendor_risk.json: mock service fixture"] --> API
    API --> VENDOR
    POLICY --> ENV["Grounded evidence envelope and PolicyResult"]
    ENV --> A["A: src.agents.single_agent"]
    ENV --> ANALYST["B: src.staged_agents.procurement_analyst"]
    ANALYST --> GATE["Analyst draft safety and policy gate"]
    GATE -->|accepted draft| REVIEWER["B: src.staged_agents.policy_risk_reviewer"]
    ENV --> REVIEWER
    A <--> GROQ["src.llm_client.GroqClient: Groq openai/gpt-oss-120b"]
    ANALYST <--> GROQ
    REVIEWER <--> GROQ
    A --> FINAL["Shared enforce_decision: grounding, policy and safety"]
    REVIEWER --> FINAL
    ENV --> FINAL
    GATE -->|rejected draft| HANDOFF["safe_handoff: preserve policy and uncertainty"]
    FINAL -->|rejected advice or provider failure| HANDOFF
    FINAL -->|validated advice| DECISION["ProcurementDecision with actual telemetry"]
    HANDOFF --> DECISION
    DECISION --> DISPLAY["Dashboard evidence, approvals, risks and next step"]
    subgraph HUMAN["Human authority outside the application"]
        APPROVERS["Manager, department, Procurement, Finance, CFO, Security, Privacy, Legal"]
        AUTH["Final purchasing authorization and sensitive exceptions"]
    end
    DISPLAY -->|recommendation and escalation only| APPROVERS
    APPROVERS --> AUTH
```

The diagram shows logical flow, not extra service calls. All five tools run once on a normal request; the reviewer reuses the original evidence and deterministic policy result rather than retrieving evidence again. Provider failures in either architecture produce a policy-preserving handoff; a rejected analyst draft does not reach the reviewer. Groq performs no arbitrary tool execution.

## Interfaces and trust boundary

- The dashboard and evaluators call the same `handle_request(request_id, architecture)` adapter. Architecture values are `single` and `staged`; unsupported values are rejected.
- `EvidenceTools` returns typed request, budget, catalog, vendor and policy results. Provenance identifies dataset records, policy sections or the vendor HTTP endpoint. The application never reads the vendor-risk fixture as an API-failure substitute.
- A returns `AgentAdvice`; B first returns `AnalystDraft`, then reviewer `AgentAdvice`. Pydantic validates structured output. Shared enforcement retains deterministic approvals, risks, missing information and human review, and verifies model evidence against the canonical evidence trail.
- Business fields, vendor responses and model drafts are untrusted. Prompts separate these from trusted instructions; safety guards reject authorization/bypass claims. These language guards are heuristic and do not replace human review.
- `RunTelemetryCounter` counts actual tool invocations and HTTP attempts, including failures/retries. Public telemetry contains tool names and call counts; evaluation artifacts also measure active time and pacing separately.
- No application component executes purchases, edits budgets, accepts contracts or completes external approvals. The final human boundary represents follow-up outside the product, not an implemented approval integration.

## Assumptions and limits

- Synthetic local datasets and the actual supplied procurement policy are the assessment ground truth. No database, production vendor integration or operating-region inference from employee country is assumed.
- The policy reference date is 2026-09-30; assessment age of 365 days is valid and 366 days is expired. Invalid/future dates remain unverified.
- Annual budget minus committed spend is available funding. Purchase history is context, not an additional deduction. Category overlap is a possible alternative, not proven equivalence or free seats.
- Missing cost does not receive a spend tier; unavailable evidence is never approval. Mandatory human review remains even for otherwise favorable advice.
- Groq credentials are operator-provided and private; analysis sends bounded request evidence to Groq. Model output and availability are nondeterministic. The mock API is local FastAPI, not a production risk service.
- The launcher uses its Python interpreter for both processes. The tested environment is Python 3.12.4 and Streamlit 1.65.0; compatibility across every version allowed by dependency ranges has not been established.
- [Final comparison](ARCHITECTURE_COMPARISON_20261011_132721_049614.md) establishes measured minimum-check and acceptance outcomes on six cases, not independently verified semantic quality. See the [decision memo](ARCHITECTURE_DECISION.md).
