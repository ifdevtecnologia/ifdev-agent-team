# Changelog

User-visible changes to this toolkit (installed instructions, agents, skills and scripts). Format
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), [Semantic Versioning](https://semver.org/).
There is no official 1.0.0 yet: versions stay in `0.y.z`. Version 0.1.0 groups everything merged
before this file existed and has no Git tag.

## Unreleased

### Added
- `dev` and `qa` global agents. Together with `senior-reviewer` they form the Dev → Reviewer → QA
  team that `claude/CLAUDE.md` now requires for every change to executable behavior.
- Global changelog rule: projects with a `CHANGELOG.md` keep the Unreleased section current and use
  `0.y.z` before an official 1.0.0.
- Validator check that read-only agents (`senior-reviewer`, `qa`) block Write and Edit.

### Changed
- `work-session` runs the Reviewer and QA roles before delivery, with a single documented exception
  for documentation-only or trivial changes.

### Upgrade
- Run `./scripts/install.sh` again after pulling: it links the two new agents (idempotent).

## 0.1.0 - 2026-10-17

### Added
- Global `CLAUDE.md`, engineering, testing and Git workflow rules.
- `senior-reviewer` agent, `work-session` and `verify-changes` skills.
- Codex `supervise-claude` skill, opt-in autonomous profile and cross-agent notifications.
- Safe, idempotent installer and uninstaller, static validator and tests.
