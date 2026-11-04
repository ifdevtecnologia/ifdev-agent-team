# Contributing

## Scope

This repository holds only **global** defaults that make sense for any project: engineering principles, testing and Git rules, general-purpose skills and agents. Product names, business domains, stack-specific business rules, project-specific reviewers and machine-specific paths belong in each project's own instructions (its `AGENTS.md` plus the tool's project files; see [docs/customization.md](docs/customization.md)), not here.

Keep instructions concise and non-contradictory. Rules live in one place (`core/rules/`); skills, agents and `core/instructions.md` reference them instead of copying them. Always-loaded files stay short; phase-specific skill procedures go in the skill's `references/` directory. See [Context efficiency](docs/development.md#context-efficiency). Do not claim token or runtime savings that were not measured.

## Editing content

Edit the tool-neutral sources in `core/` and the tool-specific files in `targets/<tool>/`, then run `python3 scripts/build_targets.py` to regenerate `dist/<tool>/` and commit the result with the source change. Never edit `dist/` by hand: the validator (`build_targets.py --check`) rejects stale, missing or extra generated files. Agents in `core/agents/` declare `read_only: true` or `false` instead of tool lists; each target maps it to its own permissions.

## Changing scripts

- `scripts/install.sh` and `scripts/uninstall.sh` must stay safe and idempotent: validate everything before mutating, never replace whole parent directories, never delete unknown content, move (not delete) anything they replace, and refuse symlinked parent directories.
- Every behavior change in a script needs automated tests in `tests/`. Tests run scripts in subprocesses with a temporary `HOME`; never run them against, or monkeypatch, your real home directory.
- Keep scripts portable to Bash 3.2 and Python 3.9+. `scripts/install.sh` and `scripts/uninstall.sh` require only the standard-library `python3` itself (no pinned dependency, no PyYAML): `scripts/lib/claude_hooks.py` uses it for the safe, structured JSON merge/removal of this toolkit's hooks in `~/.claude/settings.json`, a file the installer cannot symlink like everything else because it is user-owned and mutable. The validator and tests separately depend on the pinned PyYAML in `requirements-dev.txt` (for real, safe YAML frontmatter parsing), next to the pinned lint and coverage tools. Do not add dependencies without a concrete need; if one becomes necessary, pin it there and document it in [docs/development.md](docs/development.md#prerequisites) and CI.
- Keep the executable bit on `scripts/install.sh`, `scripts/uninstall.sh` and `scripts/validate.sh`; the validator rejects them otherwise.
- When changing the validator, add both a failing fixture and a passing counterpart (for example a forbidden instruction and the same words used as a prohibition). Never weaken a security check to make the repository pass; fix the content instead.

## Security

- Never commit credentials, tokens, keys, auth files or real personal data. Test fixtures are synthetic and generated at runtime in temporary repositories; do not add real-looking secrets or sensitive filenames to the tree.
- The validator must never open files classified as sensitive by name.

## Before opening a pull request

```bash
python3 -m pip install -r requirements-dev.txt   # once
git ls-files -z '*.sh' | xargs -0 shellcheck
ruff check scripts tests
for script in scripts/*.sh scripts/lib/*.sh; do bash -n "$script"; done
python3 scripts/build_targets.py --check
./scripts/validate.sh
python3 -m unittest discover -s tests -v
```

Add an entry under **Unreleased** in `CHANGELOG.md` for every user-visible change; a change that adds, moves or retires a managed item also adds an **Upgrade** note telling installed machines to rerun `./scripts/install.sh`.

All must pass locally; CI runs the same commands, with the tests under coverage (see [Validation and tests](docs/development.md#validation-and-tests)). Fix ShellCheck and Ruff findings instead of disabling them; an intentional exception is an inline disable comment with its reason.

## Commits and pull requests

Follow [core/rules/git-workflow.md](core/rules/git-workflow.md): a dedicated branch per task, one commit per logical change with the emoji Conventional Commits format, and a pull request that a human reviews and merges. Describe in the pull request what was validated and any risk to existing installations (renamed or removed managed paths affect every machine that installed the toolkit).
