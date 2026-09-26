# Contributing

## Scope

This repository holds only **global** defaults that make sense for any project: engineering principles, testing and Git rules, general-purpose skills and agents. Product names, business domains, stack-specific business rules, project-specific reviewers and machine-specific paths belong in each project's own `CLAUDE.md` and `.claude/` directory, not here.

Keep instructions concise and non-contradictory. Rules live in one place (`claude/rules/`); skills, agents and `claude/CLAUDE.md` reference them instead of copying them.

## Changing scripts

- `scripts/install.sh` and `scripts/uninstall.sh` must stay safe and idempotent: validate everything before mutating, never replace whole parent directories, never delete unknown content, move (not delete) anything they replace, and refuse symlinked parent directories.
- Every behavior change in a script needs automated tests in `tests/`. Tests run scripts in subprocesses with a temporary `HOME`; never run them against, or monkeypatch, your real home directory.
- Keep scripts portable to Bash 3.2 and Python 3.9+. The installer needs no Python at all; the validator and tests depend only on the pinned PyYAML in `requirements-dev.txt` (for real, safe YAML frontmatter parsing). Do not add dependencies without a concrete need; if one becomes necessary, pin it there and document it in the README and CI.
- Keep the executable bit on `scripts/install.sh`, `scripts/uninstall.sh` and `scripts/validate.sh`; the validator rejects them otherwise.
- When changing the validator, add both a failing fixture and a passing counterpart (for example a forbidden instruction and the same words used as a prohibition). Never weaken a security check to make the repository pass; fix the content instead.

## Security

- Never commit credentials, tokens, keys, auth files or real personal data. Test fixtures are synthetic and generated at runtime in temporary repositories; do not add real-looking secrets or sensitive filenames to the tree.
- The validator must never open files classified as sensitive by name.

## Before opening a pull request

```bash
python3 -m pip install -r requirements-dev.txt   # once
for script in scripts/*.sh scripts/lib/*.sh; do bash -n "$script"; done
./scripts/validate.sh
python3 -m unittest discover -s tests -v
```

All must pass locally; CI runs the same commands.

## Commits and pull requests

Follow [claude/rules/git-workflow.md](claude/rules/git-workflow.md): a dedicated branch per task, one commit per logical change with the emoji Conventional Commits format, and a pull request that a human reviews and merges. Describe in the pull request what was validated and any risk to existing installations (renamed or removed managed paths affect every machine that installed the toolkit).
