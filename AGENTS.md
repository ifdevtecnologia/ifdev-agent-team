# Toolkit development instructions

These instructions are for working **on** this repository. They are not installed. The installed content is edited in `core/` (tool-neutral instructions, rules, agents and skills) and `targets/<tool>/` (tool-specific adapters and hooks); `dist/<tool>/` is generated from them by `python3 scripts/build_targets.py`, committed and never edited by hand (see README).

## Principles

- This repository is the global source of truth for the files listed in `MANAGED_ITEMS` (`scripts/lib/common.sh`). Keep content general: no product names, business domains, project-specific reviewers or machine-specific paths.
- Preserve compatibility for machines that already installed the toolkit. Removing a managed path requires moving it to `RETIRED_ITEMS`, and moving a source to a new path requires listing its old path in `MOVED_ITEMS`, updating the validator's required files, the tests and the README, and explaining the migration in the pull request.
- Rules are defined once in `core/rules/`; other files reference them instead of duplicating content.
- `core/` is tool-neutral: it names rules, skills and roles ("the `testing` rule", "the `web-dev` skill") and says "the project's instructions"; tool paths and tool wording go in `targets/<tool>/` (adapter and agent footer). The validator enforces this.

## Scripts

- Installation and uninstallation must stay safe and idempotent: validate before mutating, move replaced content to backups, never replace whole parent directories, never remove anything that is not an exact managed link to this checkout, and refuse symlinked parents.
- Never run `scripts/install.sh` or `scripts/uninstall.sh` against your real home directory while developing; tests use a temporary `HOME` passed only to subprocesses.
- Every behavior change needs automated tests in `tests/`, following `core/rules/testing.md`.
- Every user-visible change adds an entry under **Unreleased** in `CHANGELOG.md`; adding, moving or retiring a managed item also adds an **Upgrade** note (rerun the installer).

## Validation

Run before proposing changes (requires the pinned dev dependency from `requirements-dev.txt`):

```bash
for script in scripts/*.sh scripts/lib/*.sh; do bash -n "$script"; done
python3 scripts/build_targets.py --check   # after editing core/ or targets/, run it without --check
bash scripts/validate.sh
python3 -m unittest discover -s tests -v
```

Do not weaken validator checks to make content pass. Do not open or add files that look like secrets; test fixtures are synthetic and generated in temporary directories.

## Git

Follow `core/rules/git-workflow.md` for branches, commit messages and pull requests.
