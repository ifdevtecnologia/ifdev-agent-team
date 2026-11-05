# Changelog

User-visible changes to this toolkit (installed instructions, agents, skills and scripts). Format
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), [Semantic Versioning](https://semver.org/).
There is no official 1.0.0 yet: versions stay in `0.y.z`. Version 0.1.0 groups everything merged
before this file existed and has no Git tag.

## Unreleased

### Added
- OpenAI Codex target: `install.sh --target codex` (or `--target all` for Claude Code and Codex)
  links `~/.codex/AGENTS.md` (the team process with the three rules inside, generated into
  `dist/codex/`), one custom agent per role in `~/.codex/agents/<role>.toml`, the skills in
  `~/.agents/skills/` and the shared notifier in `~/.codex/notify/`. Roles start with
  `spawn_agent` and `fork_turns: "none"`; Codex does not enforce read-only roles, so they are
  read-only by instruction and report any edit. An existing `~/.codex/AGENTS.md` is backed up and
  replaced, `~/.codex/AGENTS.override.md` only triggers a warning, and `~/.codex/config.toml` is
  never edited. The Codex `notify` adapter is shared by both targets: uninstalling one keeps it
  while the other stays installed. `work-session` stays out of Codex's implicit skill use.
  `context_budget.py` reports the always-loaded context per target.
- `install.sh --target claude` and `uninstall.sh --target claude` (repeatable; `all` means every
  available target) choose what to install or remove; `claude` is the only target so far. The
  installed targets are recorded in `~/.ifdev-ai-tools/targets`: a bare `install.sh` reinstalls
  them and a bare `uninstall.sh` removes them. A machine installed before targets were recorded is
  detected as `claude`; on a machine with nothing installed, a bare `install.sh` refuses and
  changes nothing until a target is chosen.
- Mobile-first rule for websites and web applications in the `web-dev` skill. The phone layout comes
  first: base CSS for the phone, wider screens added with `min-width` queries, touch-sized targets,
  and checks at phone width first. The global instructions, the Dev, the UX designer, the UX
  reviewer and QA now follow it, unless the project documents otherwise.
- `designer` global agent: a senior visual and brand designer for logos and monograms, visual
  identity (palette, typography, icon and favicon, lockups, usage rules) and graphic assets. It
  proposes several distinct options with their rationale, respects the user's references and
  constraints, checks legibility at small sizes and on light and dark grounds, delivers an SVG
  master with raster previews, flags font and asset licensing and writes only design files where
  the main agent says. It runs when a request creates or changes a logo, visual identity or brand
  assets, before the `ux` agent when both apply.
- `scripts/context_budget.py`: lists the files loaded in every session (`claude/CLAUDE.md` and the
  rules without `paths`) with bytes and estimated tokens (`ceil(bytes / 4)`, not a tokenizer
  count). `docs/development.md` records the current baseline as an estimate.
- Per-project customization guide (`docs/customization.md`) for teams adopting the toolkit: what
  stays global and what goes in each project, the precedence rule, how project `CLAUDE.md`,
  `AGENTS.md`, path-scoped rules, agents and skills load, which global defaults a project may
  override (no board, no `CHANGELOG.md`, stricter Git workflow, own validation commands) and
  common pitfalls. Minimal templates for a project `CLAUDE.md`/`AGENTS.md`, a path-scoped rule and
  a read-only project reviewer are in `docs/templates/project/`.
- Apache License 2.0 (`LICENSE`), mentioned in the README.
- Stack skills `flutter-dev`, `web-dev` and `supabase-dev`: guidance for Flutter apps, web front
  ends (Angular, Astro and similar) and Supabase backends (structure, state, accessibility,
  platform, security and privacy pitfalls, test levels and local-only verification). The agents
  whose work touches that stack read them; project rules still win.
- `java-spring-dev` stack skill for Java services with Spring Boot (layering, validation, error
  mapping, transactions, JPA and migrations, configuration and secrets, security, test slices).
- `safe-refactor` skill: refactoring without behavior change (confirm the reason, characterization
  tests first, small verified steps, refactor separate from behavior changes). The Dev reads it
  when the task is a refactoring.
- `e2e-testing` skill: acceptance and end-to-end tests derived from acceptance criteria (choice of
  level, stable selectors and waits, isolated synthetic data, flakiness, local-only runs). QA reads
  it before writing acceptance tests.
- "Reuse and simplicity" section in `engineering-principles.md`: search for existing code before
  writing new code, extract abstractions only when they pay off, and simplify the change once the
  tests pass.
- Project board rule in `git-workflow.md`: a product that uses a GitHub Project tracks its
  development work there with the statuses Backlog, To Do, Doing, Review, To Tests, Testing, To
  Homologation, Homologation, To Deploy and Done. With delivery authorization the main agent creates
  the task's issue and moves it up to To Homologation (and back to Doing on findings); Homologation,
  To Deploy and Done follow the human merge, homologation approval and authorized deployment.
- `debugger` global agent: a read-only senior debugger that reproduces a bug, failing check or
  regression with unknown cause, finds the root cause with evidence and reports the proposed fix and
  the regression test, before the Dev. It never edits files or touches the user's working tree.
- `api-reviewer` global agent: a read-only specialized review that runs alongside the Reviewer only
  when the change adds or changes a contract consumed outside its deployable unit or published
  (cross-service endpoints and events, webhooks, public interfaces) or an external integration:
  compatibility, versioning, errors, idempotency, pagination and limits.
- `metrics-analyst` global agent: defines success, input and guardrail metrics, tracking plans,
  dashboards and experiment designs for a goal or feature, and analyzes only aggregated or synthetic
  data, writing only analytics documents where the main agent says.
- `po` global agent: a senior product owner that turns a product goal or idea into epics and user
  stories with story-level acceptance criteria, a prioritized backlog with reasons and a release or
  MVP cut, writing only product files where the main agent says. It runs before the team when the
  user asks for stories, a backlog, a roadmap or prioritization; the `analyst` accepts a confirmed
  story as input.
- `tech-writer` global agent: a senior technical writer for documentation that needs more than a
  changelog entry (user guides, README and setup, API references, runbooks, release notes). It
  verifies what it documents against code and tests and edits only documentation files.
- `privacy-reviewer`, `db-reviewer` and `performance-reviewer` global agents: read-only specialized
  reviews that run alongside the Reviewer only when the change touches personal data flows (purpose,
  consent, minimization, retention, data subject rights, sharing); the schema, migrations, database
  functions or access policies; or hot paths, large data, caching, concurrency or a performance
  target, respectively.
- `architect` global agent: a read-only senior software architect that runs after the Analyst only
  when a change needs a technical decision (new modules or services, integrations, data model or
  tenancy changes, cross-cutting concerns, technology choices, scale or cost). It compares options
  and proposes a recommendation, boundaries and contracts, risks, an implementation slicing and a
  draft decision record. The Dev follows the confirmed decision and the Reviewer checks the diff
  against it.
- `devops` global agent: a senior DevOps engineer that holds the Dev role for CI/CD, build and
  container configuration, infrastructure as code, environments, observability (logs, metrics,
  traces, alerts, SLOs) and release automation, or works with the Dev on separate slices when a
  change spans application and infrastructure. It verifies only locally (lint, build, dry run or
  plan against local or mocked state), treats any command that contacts a real environment as a
  remote step, never deploys or applies, and lists the remote steps that need the user's
  authorization.
- `ux` global agent: a senior UX designer that turns the objective and acceptance criteria of a
  new or redesigned user-facing flow into flows, a screen inventory with every state, interaction
  and accessibility notes, copy and, when asked, a low/mid-fidelity prototype. It writes only design
  files in the location the main agent names and never edits production code, tests or
  configuration. The team in `claude/CLAUDE.md` and `work-session` run it after the Analyst when a
  request creates or substantially changes screens or navigation, and the `ux-reviewer` checks the
  implementation against the confirmed design.
- `dev` and `qa` global agents. Together with `reviewer` they form the Dev → Reviewer → QA
  team that `claude/CLAUDE.md` now requires for every change to executable behavior.
- Global changelog rule: projects with a `CHANGELOG.md` keep the Unreleased section current and use
  `0.y.z` before an official 1.0.0.
- Validator check that read-only agents (`analyst`, `architect`, `reviewer`, `ux-reviewer`,
  `security-reviewer`, `api-reviewer`, `privacy-reviewer`, `db-reviewer`, `performance-reviewer`,
  `debugger`, `qa`) block Write and Edit.
- `analyst` global agent: a read-only senior requirements analyst that turns a new feature, a
  business-rule change or an ambiguous request into scope, numbered testable acceptance criteria,
  blocking questions, recorded assumptions, risks and a suggested slicing.
- `ux-reviewer` and `security-reviewer` global agents: read-only specialized reviews that run
  alongside the Reviewer only when the change alters a user interface, or touches authentication,
  data access, secrets, permissions, backup or transfer configuration, or dependencies,
  respectively. Project-specific reviewers complement them.
- Validator check that every required or read-only agent is also listed in `MANAGED_ITEMS`.

### Changed
- A product's GitHub Project is now the complete record of its work: every executed or planned
  task has an issue, including manual operations and external setup. A knowledge-base page named
  by the project's instructions (for example a Notion page) holds the product's general view. The
  project `AGENTS.md` template gains a **Work tracking** section for these links.
- Shorter, tool-agnostic README: what the team is, a flow diagram of the roles, compact role,
  skill and supported-tool tables, a build-and-install diagram and a quick start for Claude Code
  and Codex. The details moved to new guides: `docs/installation.md` (prerequisites, targets,
  installer guarantees, backups and manual restoration, upgrade, uninstall, what the toolkit owns,
  troubleshooting), `docs/team.md` (every role, when it runs and what it never does),
  `docs/notifications.md` and `docs/development.md` (layout, validator, context budget baseline).
  `docs/customization.md` and the project templates now start from `AGENTS.md`, which every tool
  reads and a short `CLAUDE.md` imports.
- `install.sh` also refuses, before changing anything, a managed source below a symlinked
  directory of the checkout, so every installed link loads content from the checkout itself.
- New source layout: the tool-neutral instructions, rules, agents and skills live in `core/`,
  tool-specific files (the Claude hook, the Codex `notify` adapter) in `targets/`, and the files
  Claude Code loads (`CLAUDE.md` and the agents) are generated into `dist/claude/` by
  `scripts/build_targets.py` and committed. Agents declare `read_only` instead of tool lists. Claude
  Code loads the same files with the same agent tool lists, and the same thirty-three destinations
  are installed. The validator checks that `dist/` is up to date.
- The instructions, rules, agents and skills no longer name Claude Code paths or tools: they refer
  to rules, skills and roles by name ("the `testing` rule", "the `web-dev` skill"), say "the
  project's instructions" (defined once in the global instructions as `AGENTS.md` plus the tool's
  own project files, such as `CLAUDE.md` and `.claude/`) and "the request that invoked this skill"
  instead of `$ARGUMENTS`. The meaning of every rule and role is unchanged. For Claude Code, a short
  note at the top of `~/.claude/CLAUDE.md` and a footer in every agent say that roles are the
  subagents of the same name and where the rules and skills are. The global instructions also say
  what to do in a tool that cannot start a role in a fresh context: a new session per role with a
  written hand-off, or report "independent review not performed" and PARTIAL. The validator
  rejects tool-specific paths and wording in `core/`.
- The opt-in Codex notify adapter uses the shared notifier in `~/.codex/notify/` when it is
  installed there and falls back to the one in `~/.claude/hooks/`.
- The project is renamed from `ifdev-ai-tools` to `ifdev-agent-team`. GitHub redirects the old URL,
  existing clones keep working, and the state directory stays `~/.ifdev-ai-tools`. Rerun
  `./scripts/install.sh` only if you move or rename your local checkout.
- Project board rule: new `Canceled` status after Done for discarded items. Canceling is the
  user's decision; the agent proposes it with the reason, closes the issue as not planned once the
  user agrees and only proposes closing the open pull request and branch.
- `qa` now writes the missing automated acceptance tests (integration, component or end-to-end,
  with the project's existing tooling) derived from the acceptance criteria, in test files only. It
  never edits production code, configuration or dependencies; a failing acceptance test is a
  finding for the Dev, and the Reviewer reviews the tests QA adds before delivery.
- `dev` searches for code to reuse before writing, reads the matching stack skill and simplifies
  the change once the tests pass; the `reviewer` reports missed reuse and abstractions without a
  real problem, with the simpler alternative.
- The `security-reviewer` leaves purpose, consent, minimization and retention to the
  `privacy-reviewer` and migration safety to the `db-reviewer` when they run.
- The `dev` and `devops` agents receive the confirmed architecture decision when the Architect ran,
  and the `reviewer` checks the diff against it; `work-session` runs the Architect after the Analyst
  when the team calls for it.
- The `dev`, `ux-reviewer` and `qa` agents receive the confirmed design when the UX designer ran:
  the Dev follows it, the UX reviewer checks the implementation against it and QA uses its screen
  states as scenarios.
- The `senior-reviewer` agent is renamed to `reviewer`.
- The `dev`, `reviewer` and `qa` agents are explicitly senior roles: their definitions now state the
  expected engineering judgment, risk focus and clear reporting.
- `install.sh` and `uninstall.sh` remove links left by retired items when they still point to this
  checkout, and report the count; anything else at a retired path is kept untouched.
- `work-session` runs the Reviewer and QA roles before delivery, with a single documented exception
  for documentation-only or trivial changes.
- The team flow in `claude/CLAUDE.md` is now Analyst → Dev → Reviewer → QA. The Analyst runs
  first for new features, business-rule changes and ambiguous requests, and is skipped for small
  fixes, text or style adjustments, refactors without behavior change and requests with clear
  acceptance criteria. Blocking questions go to the user; without an answer, only unblocked
  slices proceed and the result is PARTIAL (or BLOCKED). Reviewer and QA stay mandatory; the UX and
  security reviewers join the Reviewer only when their criteria apply, and never for
  documentation-only or trivial changes.
- `work-session` runs the Analyst before planning when the request needs requirements, and the
  `dev` and `qa` agents use the Analyst's acceptance criteria when it ran.
- The Claude Code `Stop` hook always sends its "complete" notification; the
  `IFDEV_SUPERVISED_CLAUDE` variable no longer silences it.
- The validator treats a retired item's source as gone when only Git-ignored leftovers (such as
  `__pycache__/`) remain in the checkout.

### Removed
- Codex supervision: the `supervise-claude` Codex skill (with its references and helper scripts)
  and the opt-in `ifdev-autonomous` Codex profile. Complete deliveries now run in Claude Code with
  the Analyst → Dev → Reviewer → QA team. The Codex `notify` adapter and the shared notifier stay.
- Validator checks that only applied to the supervision: the `codex/profiles/` settings checks and
  the exception that let the supervisor skill forbid Claude from delivering.

### Upgrade
- To use the toolkit with Codex, run `./scripts/install.sh --target codex` (once; the target is then
  recorded and a bare `./scripts/install.sh` reinstalls it). A Claude-only install needs no action;
  on one made before targets were recorded, adding Codex records `claude` too.
  Review `./scripts/install.sh --target codex --dry-run` first if you keep your own
  `~/.codex/AGENTS.md`: it is moved to the backups, not merged.
- `git pull` keeps existing installations working: the old paths in `claude/` and `codex/` are now
  compatibility links to the new locations, kept for one release. Run `./scripts/install.sh` once
  after pulling to move your links to the new sources; links to the old paths are relinked without
  backups and the `claude` target is recorded. On a new machine, install with
  `./scripts/install.sh --target claude`.
- Run `./scripts/install.sh` again after pulling (idempotent): it links the `po`, `metrics-analyst`,
  `analyst`, `architect`, `ux`, `designer`, `debugger`, `dev`, `devops`, `tech-writer`, `reviewer`,
  `ux-reviewer`, `security-reviewer`, `api-reviewer`, `privacy-reviewer`, `db-reviewer`,
  `performance-reviewer` and `qa` agents in `~/.claude/agents/` and the `flutter-dev`, `web-dev`,
  `supabase-dev`, `java-spring-dev`, `safe-refactor` and `e2e-testing` skills in
  `~/.claude/skills/`, and removes old links that still point to this checkout:
  `senior-reviewer.md` in `~/.claude/agents/`, the `supervise-claude` link in the Codex skills
  directory and `~/.codex/ifdev-autonomous.config.toml`. A real directory, a
  file or a link elsewhere at those paths is kept and reported. Until then the main agent reports
  the missing agents and runs their roles in a separate context. Stop selecting `codex --profile
  ifdev-autonomous`. A leftover `codex/skills/supervise-claude/` directory with only ignored
  `__pycache__/` files may remain in the checkout; it is unused and safe to delete.

## 0.1.0 - 2026-10-17

### Added
- Global `CLAUDE.md`, engineering, testing and Git workflow rules.
- `senior-reviewer` agent, `work-session` and `verify-changes` skills.
- Codex `supervise-claude` skill, opt-in autonomous profile and cross-agent notifications.
- Safe, idempotent installer and uninstaller, static validator and tests.
