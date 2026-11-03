# The team

Eighteen global roles, all senior, form the team described in [`core/instructions.md`](../core/instructions.md#team-analyst--dev--reviewer--qa). That file is the source of truth for when each role runs; this page is a reader's summary. The [README](../README.md#-how-the-team-works) has the flow diagram.

## When each role runs

- **Main agent:** orchestrates every step, takes blocking questions to the user, fixes material findings and delivers through the [Git workflow](../core/rules/git-workflow.md). A request that needs no optional role goes straight to the Dev; documentation-only or trivial changes stop at the Reviewer.
- **Product owner:** before the team when the user asks for stories, a backlog, a roadmap or prioritization; each confirmed story then enters the team. The **metrics analyst** defines how it will be measured when needed.
- **Debugger:** before the Dev when the cause of a bug is unknown.
- **Analyst:** before the Dev only when the request needs requirements; its acceptance criteria guide the Dev and QA.
- **Architect:** after the Analyst only when the change needs a technical decision; its confirmed decision guides the Dev and the Reviewer.
- **UX designer:** after the Analyst only when the request creates or substantially changes screens or navigation; its confirmed design guides the Dev, the UX reviewer and QA.
- **Visual designer:** when a request creates or changes a logo, visual identity or brand assets, before the UX designer when both apply.
- **DevOps:** takes the Dev role (or works with the Dev on separate slices) when the change is mainly infrastructure, delivery automation or observability.
- **Technical writer:** documentation that needs more than a changelog entry, before the Reviewer.
- **Specialized reviewers** (UX, security, API, privacy, database, performance): alongside the Reviewer, only when the diff meets their criterion in `core/instructions.md`: interface changes; authentication, data access, secrets, permissions, backup or transfer configuration, or dependencies; contracts and external integrations; personal data flows; schema, migrations or access policies; hot paths, large data or performance targets.
- **QA:** after the review, against the acceptance criteria.

## Roles

| Role | Agent | Does | Never does |
|---|---|---|---|
| Product owner | [`po`](../core/agents/po.md) | Turns a product goal or idea into epics and user stories with acceptance criteria, a prioritized backlog with reasons and a release or MVP cut, writing only product files where the main agent says | Edit code, tests or configuration, create items in external trackers, invent business rules or targets, commit, push |
| Metrics analyst | [`metrics-analyst`](../core/agents/metrics-analyst.md) | Success, input and guardrail metrics with exact definitions, tracking plans, dashboards and experiment designs, and analysis of aggregated or synthetic data, writing only analytics documents where the main agent says | Edit code, query production or analytics platforms, analyze identifiable data, commit, push |
| Analyst | [`analyst`](../core/agents/analyst.md) | Read-only requirements for new features, rule changes or ambiguous requests: scope, numbered testable acceptance criteria, blocking questions, recorded assumptions, risks and slicing | Implement, design detailed architecture, invent business rules, remote operations |
| Architect | [`architect`](../core/agents/architect.md) | Read-only technical decisions when a change needs one: drivers and constraints, options compared, recommendation, boundaries and contracts, data model, security boundaries, risks, implementation slicing and a draft decision record | Implement, write files, invent business rules or budgets, remote operations |
| UX designer | [`ux`](../core/agents/ux.md) | Flows, screen inventory with every state, interaction and accessibility notes, copy and, when asked, a low/mid-fidelity prototype for new or redesigned screens, writing only design files where the main agent says | Edit production code, tests or configuration, invent business rules or brand, publish, remote operations |
| Visual designer | [`designer`](../core/agents/designer.md) | Logos and monograms, visual identity (palette, typography, icon and favicon, lockups, usage rules) and graphic assets: several distinct options with rationale, an SVG master and raster previews checked at small sizes and on light and dark grounds, and font and asset licensing, writing only design files where the main agent says | Edit production code, tests or configuration, copy third-party marks, invent names or claims, publish, remote operations |
| Debugger | [`debugger`](../core/agents/debugger.md) | Read-only investigation of a bug, failing check or regression with unknown cause: local reproduction, evidence, root cause, proposed fix and the regression test | Edit, apply fixes, touch the user's working tree, remote access |
| Dev | [`dev`](../core/agents/dev.md) | Implements the planned change with tests and runs the affected checks | Commit, push, deploy, remote changes |
| DevOps | [`devops`](../core/agents/devops.md) | Holds the Dev role for CI/CD, build and container configuration, infrastructure as code, environments, observability (logs, metrics, traces, alerts, SLOs) and release automation; verifies only locally (lint, build, dry run or plan against local or mocked state) and lists the remote steps that need authorization | Deploy, apply, release or act on any remote environment, read credentials, commit, push |
| Technical writer | [`tech-writer`](../core/agents/tech-writer.md) | User guides, README and setup, API references, runbooks and release notes for a change, verified against code and tests, editing only documentation files | Edit code, tests or configuration, publish documentation sites, commit, push |
| Reviewer | [`reviewer`](../core/agents/reviewer.md) | Independent read-only review of the diff | Edit or run application scripts |
| UX reviewer | [`ux-reviewer`](../core/agents/ux-reviewer.md) | Read-only review of interface changes: loading/empty/error states, action feedback, accessibility, i18n, consistency across clients, small screens, copy, and conformance to the confirmed design when the UX designer ran | Edit, run builds, remote operations |
| Security reviewer | [`security-reviewer`](../core/agents/security-reviewer.md) | Read-only review of auth, authorization and data isolation, secrets in clients, untrusted input, personal data and logs, dependencies, platform permissions, with concrete attack scenarios | Edit, run exploits, remote operations, read credentials |
| API reviewer | [`api-reviewer`](../core/agents/api-reviewer.md) | Read-only review of contracts and integrations: compatibility and versioning, errors, idempotency and webhook delivery, pagination and limits, contract tests | Edit, call external APIs or sandboxes, remote operations |
| Privacy reviewer | [`privacy-reviewer`](../core/agents/privacy-reviewer.md) | Read-only review of personal and sensitive data: purpose and legal basis, consent, minimization, retention and deletion, data subject rights, sharing and transfers, analytics | Edit, give legal opinions, read real personal data, remote operations |
| Database reviewer | [`db-reviewer`](../core/agents/db-reviewer.md) | Read-only review of schema and migrations (data loss, locking, rollback), constraints, indexes and queries, transactions, functions and access policies | Edit, run migrations, connect to databases, remote operations |
| Performance reviewer | [`performance-reviewer`](../core/agents/performance-reviewer.md) | Read-only review of hot paths, round trips, payload and bundle size, client rendering and memory, caching, concurrency and cost, against realistic volumes | Edit, run benchmarks or builds, remote operations |
| QA | [`qa`](../core/agents/qa.md) | Derives scenarios from the acceptance criteria, writes the missing acceptance tests (integration, component or end-to-end), reruns the local verification and reports gaps and a verdict | Production code, configuration or dependencies, destructive or remote operations |

## Project reviewers and name clashes

Project-specific reviewers (for example access-policy reviewers) complement the global ones and do not replace them; see [customization.md](customization.md#project-reviewers). A project can add its own agents, but must not name them `po`, `metrics-analyst`, `analyst`, `architect`, `ux`, `designer`, `debugger`, `dev`, `devops`, `tech-writer`, `reviewer`, `ux-reviewer`, `security-reviewer`, `api-reviewer`, `privacy-reviewer`, `db-reviewer`, `performance-reviewer` or `qa`: a project agent takes precedence over a global one with the same name and would hide it.

## Skills

The stack skills [`flutter-dev`](../core/skills/flutter-dev/SKILL.md), [`web-dev`](../core/skills/web-dev/SKILL.md), [`supabase-dev`](../core/skills/supabase-dev/SKILL.md) and [`java-spring-dev`](../core/skills/java-spring-dev/SKILL.md) give the roles whose work touches a stack (Dev, Reviewer, QA, the UX designer and UX reviewer, and the database and security reviewers) stack-specific guidance: structure, state, accessibility, platform and security pitfalls, test levels and local verification. They name technologies, not products, and are read only when a change touches that stack; a project's own rules and skills win when they are more specific.

Two procedure skills complement them: [`safe-refactor`](../core/skills/safe-refactor/SKILL.md) (characterization tests first, small verified steps, refactor separate from behavior changes) for the Dev, and [`e2e-testing`](../core/skills/e2e-testing/SKILL.md) (acceptance and end-to-end tests from acceptance criteria) for QA. [`verify-changes`](../core/skills/verify-changes/SKILL.md) selects and runs the local checks, and [`work-session`](../core/skills/work-session/SKILL.md) runs a bounded objective with a plan and a resume report.

## After an update

A release that adds managed items (such as new agents) needs `./scripts/install.sh` again to create the new links; see [installation.md](installation.md#upgrade).
