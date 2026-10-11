# Architecture decision: select B

Employees need evidence-backed software procurement advice while mandatory approvals remain with humans. Select Architecture B, the staged Procurement Analyst and Policy/Risk Reviewer, as directed by the project architect. Keep Architecture A as the comparison baseline.

A uses one Groq completion to analyze shared evidence and deterministic policy. B uses an analyst completion, validates its draft, and passes the draft with original evidence and policy to a reviewer completion. Both use the same five tools and final enforcement; neither can authorize purchasing.

The final Stage 6D comparison ran each of six unchanged public cases once per architecture using Groq openai/gpt-oss-120b. Retries were disabled, attempts capped at 18, and calls globally paced at 60 seconds. Paired evidence/policy snapshots matched. Active execution excludes evaluation pacing.

| Metric | A: Single | B: Staged |
|---|---:|---:|
| Public minimum checks | 6/6 | 6/6 |
| Accepted AI outputs | 4/6 | 5/6 |
| Safe fallbacks | 2 | 1 |
| Groq HTTP attempts | 6 | 12 |
| Tool calls | 30 | 30 |
| Mean active latency | 1.890 s | 3.584 s |
| Total active latency | 11.337 s | 21.502 s |

All 12 final decisions preserved mandatory human controls and passed public minimum checks, including fallbacks. The full run used 18 model attempts and 60 tool invocations; wall time was 1022.331 seconds, approximately 17 minutes, including 989.490 seconds of pacing. Provider responses were all HTTP 200.

B provides an explicit separation between procurement analysis and policy/risk review, and accepted one additional AI output in this run. This modest improvement supports the architect's choice, without proving better reasoning. A is faster and more economical in request usage, requiring half the HTTP attempts; monetary cost was not measured.

Six public cases are a small sample. Model output is nondeterministic, and fixed evaluation order introduces potential bias. Three safety rejections occurred: two in A and one in B's reviewer. Rejection predicates do not establish whether the wording was genuinely unsafe or falsely rejected. Semantic recommendation quality has not been independently established; no statistically proven superiority is claimed.

Deterministic budget, approvals, risk flags, missing information, provenance and human review remain authoritative. Residual risks include heuristic language guards, uncertain vendor evidence and provider availability. Sensitive exceptions and final purchasing authorization require humans. Both implementations remain available; the public handler default and dashboard initial selection are unchanged.

Source: [Stage 6D JSON](ARCHITECTURE_COMPARISON_20261011_132721_049614.json) and [report](ARCHITECTURE_COMPARISON_20261011_132721_049614.md).
