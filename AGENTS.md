# Toolkit development instructions

These instructions are for working **on** this repository. They are not installed; the installed global instructions live in `claude/` and `codex/` (see README).

## Principles

- This repository is the global source of truth for the files listed in `MANAGED_ITEMS` (`scripts/lib/common.sh`). Keep content general: no product names, business domains, project-specific reviewers or machine-specific paths.
- Preserve compatibility for machines that already installed the toolkit. Renaming or removing a managed path requires updating the installer, the validator's required files, the tests and the README, and explaining the migration in the pull request.
- Rules are defined once in `claude/rules/`; other files reference them instead of duplicating content.

## Scripts

- Installation and uninstallation must stay safe and idempotent: validate before mutating, move replaced content to backups, never replace whole parent directories, never remove anything that is not an exact managed link to this checkout, and refuse symlinked parents.
- Never run `scripts/install.sh` or `scripts/uninstall.sh` against your real home directory while developing; tests use a temporary `HOME` passed only to subprocesses.
- Every behavior change needs automated tests in `tests/`, following `claude/rules/testing.md`.

## Validation

Run before proposing changes (requires the pinned dev dependency from `requirements-dev.txt`):

```bash
for script in scripts/*.sh scripts/lib/*.sh; do bash -n "$script"; done
bash scripts/validate.sh
python3 -m unittest discover -s tests -v
```

Do not weaken validator checks to make content pass. Do not open or add files that look like secrets; test fixtures are synthetic and generated in temporary directories.

## Git

Follow `claude/rules/git-workflow.md` for branches, commit messages and pull requests.
