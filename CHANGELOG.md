# Changelog

User-visible changes to this toolkit (installed instructions, agents, skills and scripts). Format
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), [Semantic Versioning](https://semver.org/).
There is no official 1.0.0 yet: versions stay in `0.y.z`. Version 0.1.0 groups everything merged
before this file existed and has no Git tag.

## Unreleased

### Added
- `dev` and `qa` global agents. Together with `reviewer` they form the Dev → Reviewer → QA
  team that `claude/CLAUDE.md` now requires for every change to executable behavior.
- Global changelog rule: projects with a `CHANGELOG.md` keep the Unreleased section current and use
  `0.y.z` before an official 1.0.0.
- Validator check that read-only agents (`analyst`, `reviewer`, `ux-reviewer`, `security-reviewer`,
  `qa`) block Write and Edit.
- `analyst` global agent: a read-only senior requirements analyst that turns a new feature, a
  business-rule change or an ambiguous request into scope, numbered testable acceptance criteria,
  blocking questions, recorded assumptions, risks and a suggested slicing.
- `ux-reviewer` and `security-reviewer` global agents: read-only specialized reviews that run
  alongside the Reviewer only when the change alters a user interface, or touches authentication,
  data access, secrets, permissions, backup or transfer configuration, or dependencies,
  respectively. Project-specific reviewers complement them.
- Validator check that every required or read-only agent is also listed in `MANAGED_ITEMS`.

### Changed
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
- Run `./scripts/install.sh` again after pulling (idempotent): it links the `analyst`, `dev`,
  `reviewer`, `ux-reviewer`, `security-reviewer` and `qa` agents in `~/.claude/agents/`, and
  removes old links that still point to this checkout: `senior-reviewer.md` in
  `~/.claude/agents/`, the `supervise-claude` link in the Codex skills directory and
  `~/.codex/ifdev-autonomous.config.toml`. A real directory, a file or a link elsewhere at those
  paths is kept and reported. Until then the main agent reports the missing agents and runs their
  roles in a separate context. Stop selecting `codex --profile ifdev-autonomous`. A leftover
  `codex/skills/supervise-claude/` directory with only ignored `__pycache__/` files may remain in
  the checkout; it is unused and safe to delete.

## 0.1.0 - 2026-10-17

### Added
- Global `CLAUDE.md`, engineering, testing and Git workflow rules.
- `senior-reviewer` agent, `work-session` and `verify-changes` skills.
- Codex `supervise-claude` skill, opt-in autonomous profile and cross-agent notifications.
- Safe, idempotent installer and uninstaller, static validator and tests.
