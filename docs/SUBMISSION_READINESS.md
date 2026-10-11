# Submission readiness

This checklist maps the supplied [course assessment](../resources/FDE_Assessment_3.pdf) and [starter brief](Assignment_3_Brief.pdf) to repository deliverables. It records preparation, not final submission.

| Mandatory deliverable | Repository evidence |
|---|---|
| Public GitHub repository and clear setup/run README | [README](../README.md); repository URL supplied by owner: https://github.com/D4RK-ctrl/FDE-03. Public visibility and publication of Stage 7 changes are not verified here. |
| Functional local application with one-command launch | [launcher](../run_local.py), [dashboard](../app.py); `python run_local.py` starts API and UI. Browser acceptance remains with the user. |
| UI showing request details, evidence, recommendation and next action | [dashboard](../app.py), [presentation helpers](../src/ui_presentation.py), [UI tests](../tests/test_ui.py). |
| At least three real tools, including a deterministic tool | Five [EvidenceTools](../src/tools.py); shared [policy engine](../src/policy.py). |
| Working single agent and comparison with staged variant on shared cases | [single agent](../src/agents.py), [staged agents](../src/staged_agents.py), [adapter](../src/solution.py). |
| Structured output with recommendation, evidence, approvals, missing information, risk flags, next step and human handoff | [public contract](../src/contracts.py), [enforcement](../src/decision_validation.py). |
| Workflow/architecture diagram and assumptions | [Architecture](ARCHITECTURE.md). |
| Reproducible evaluation script and results, including policy/evidence/escalation checks and latency/call counts | [comparison runner](../evals/compare_architectures.py), [evaluation guide](../evals/README.md), [final JSON](ARCHITECTURE_COMPARISON_20261011_132721_049614.json), [final Markdown](ARCHITECTURE_COMPARISON_20261011_132721_049614.md). |
| Architecture decision memo, maximum 500 words | [Decision memo](ARCHITECTURE_DECISION.md); B selected, A retained. |
| No committed secrets; environment template where needed | [.env.example](../.env.example), [.gitignore](../.gitignore); private `.env` remains ignored and untracked. |

Neither supplied PDF requires screenshots. The original [student checklist](../STUDENT_CHECKLIST.md) and templates remain reference materials.

## Owner actions before submission

1. Review the local Stage 7 changes and approve documentation; complete manual dashboard verification if outstanding.
2. Publish reviewed changes through the project's branch/PR workflow. No commit, push, merge or PR was performed during Stage 7.
3. Confirm the GitHub repository is public and the submitted revision includes these documents and preserved results.
4. The course PDF specifies **October 8, 2026** as the deadline; this preparation is dated October 11, 2026. Confirm late-submission acceptance with the course.
5. Submit the public GitHub URL through the course's [Google Form](https://forms.gle/ab9DrohuNQWukXCD9). The form and repository visibility were not opened or checked in this task.

## Limits to the evidence

All twelve final decisions passed public minimum checks, including three safe fallbacks. Those passes do not establish accepted AI analysis or independent semantic correctness. The sample is six public cases, output is nondeterministic, and order is fixed. Three safety predicates rejected advice; the artifacts do not determine unsafe wording versus false positives. No production deployment, autonomous procurement, hidden-case guarantee or statistical superiority is claimed.
