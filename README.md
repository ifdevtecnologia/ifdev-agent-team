# ifdev-ai-tools

Versioned, reproducible source of truth for **global** Claude Code and Codex instructions: engineering principles, testing and Git rules, a review agent and a few general-purpose skills. The installer links these files into `$HOME` so every machine uses the same reviewed content.

## Global versus project instructions

This toolkit only provides defaults that apply to every repository. Each project keeps its own `CLAUDE.md` (and `AGENTS.md` when used) plus `.claude/rules/`, `.claude/skills/` and `.claude/agents/` for local behavior: stack, commands, domain rules and specialized reviewers. Project instructions take precedence when they are more specific or explicitly conflict with the global defaults.

Two different `CLAUDE.md` files live here:

- [`claude/CLAUDE.md`](claude/CLAUDE.md) is the **installed** global instruction file (linked to `~/.claude/CLAUDE.md`).
- [`CLAUDE.md`](CLAUDE.md) at the repository root (with [`AGENTS.md`](AGENTS.md)) contains instructions for **developing this toolkit** and is never installed.

## Layout

```
claude/
  CLAUDE.md                          -> ~/.claude/CLAUDE.md
  rules/engineering-principles.md    -> ~/.claude/rules/engineering-principles.md
  rules/testing.md                   -> ~/.claude/rules/testing.md
  rules/git-workflow.md              -> ~/.claude/rules/git-workflow.md
  agents/senior-reviewer.md          -> ~/.claude/agents/senior-reviewer.md
  skills/work-session/               -> ~/.claude/skills/work-session
  skills/verify-changes/             -> ~/.claude/skills/verify-changes
codex/
  skills/supervise-claude/           -> ~/.codex/skills/supervise-claude
scripts/
  install.sh, uninstall.sh           link / unlink the eight items above
  lib/common.sh                      managed item list and shared safety helpers
  validate.sh, validate.py           static repository validation
tests/                               isolated automated tests (Python unittest)
requirements-dev.txt                 pinned development dependency (PyYAML) for validation/tests
.github/workflows/validate.yml       CI: syntax, validator and tests
```

## Prerequisites

- Bash (3.2 or newer), `git` and standard POSIX utilities (`ln`, `mv`, `mkdir`, `readlink`, `mktemp`). This is all the installer needs.
- For validation and tests only: Python 3.9+ and PyYAML 6.0.3, pinned in [`requirements-dev.txt`](requirements-dev.txt). Install it with `python3 -m pip install -r requirements-dev.txt`, inside a virtual environment if your system Python is externally managed.
- Claude Code and/or Codex installed separately; this toolkit does not install or configure them.

## Install on a new machine

```bash
git clone https://github.com/fagnerlima/ifdev-ai-tools.git
cd ifdev-ai-tools
./scripts/install.sh --dry-run   # review the plan; changes nothing
./scripts/install.sh
./scripts/validate.sh            # optional: validate the checkout (needs the dev dependency)
```

The entry-point scripts are versioned with the executable bit (the validator enforces it). If your checkout lost it, `bash scripts/install.sh` works the same way.

The clone location is free: links point to wherever the repository lives, so keep the checkout in place after installing. Updating is a normal `git pull`; the links already point to the updated files. If you move the checkout, run `./scripts/install.sh` again from the new location (the old, now dangling links are backed up and replaced).

### What the installer does

- Uses only `$HOME`, without elevated privileges, and resolves the repository root from the script's own location.
- Creates symlinks for exactly the eight destinations above and the missing parent directories. It never links or replaces `~/.claude`, `~/.claude/skills`, `~/.claude/agents` or `~/.codex/skills` as a whole, so unknown siblings (your settings, other skills and agents) are preserved.
- Checks prerequisites, every source, every destination and every parent directory before changing anything. It refuses symlinked parent directories (for example `~/.claude/rules` pointing elsewhere) to avoid writing outside `$HOME` or into third-party trees.
- A destination that already links to this repository is left as is. A real file, a directory, a foreign symlink or a dangling symlink is **moved** (not deleted) to a unique backup directory before linking.
- Is idempotent: a second run reports every item as unchanged and creates no backup.
- Uses a lock directory (`~/.ifdev-ai-tools/lock`) so concurrent runs cannot collide. If a run is interrupted hard, the message explains how to remove the stale lock after confirming no run is active.
- `--dry-run` prints the plan and makes no changes at all (no directories, backups or locks).

### Backups and manual restoration

Backups go to `~/.ifdev-ai-tools/backups/YYYYMMDD-HHMMSS-<unique>/`, preserving the relative path, for example `.../.claude/CLAUDE.md` or `.../.codex/skills/supervise-claude/`. Each backup directory has a `MANIFEST` listing the moved paths. The toolkit never restores backups automatically; restoration is manual and deliberate, one `MANIFEST` entry at a time:

```bash
./scripts/uninstall.sh                          # remove this toolkit's links first
backup="$HOME/.ifdev-ai-tools/backups/<chosen-backup>"
cat "$backup/MANIFEST"                          # review what was moved
item=".claude/CLAUDE.md"                        # one entry from MANIFEST
# The destination must be completely absent: no file, directory or (even dangling) symlink.
if [ -e "$HOME/$item" ] || [ -L "$HOME/$item" ]; then
  echo "conflict: $HOME/$item exists; resolve it manually, nothing was moved" >&2
else
  mv -n -- "$backup/$item" "$HOME/$item"
fi
```

Repeat the check for every item; never move a backup over an existing destination. On a conflict, compare both versions and decide yourself what to keep (for example rename the current one first); the backup stays where it is until you do. Backups are never removed automatically; delete them yourself once you no longer need them.

## Uninstall

```bash
./scripts/uninstall.sh --dry-run
./scripts/uninstall.sh
```

Removes only symlinks that point exactly to this checkout's managed items. Real files, directories, symlinks to other locations, sibling configuration, parent directories and backups are kept. It refuses to operate through symlinked parent directories (including `~/.ifdev-ai-tools` and its `backups/` directory, checked before anything else) and lists existing backups at the end.

## Validation and tests

```bash
python3 -m pip install -r requirements-dev.txt   # once; pinned PyYAML
for script in scripts/*.sh scripts/lib/*.sh; do bash -n "$script"; done
./scripts/validate.sh
python3 -m unittest discover -s tests -v
```

`validate.sh` is a bounded static lint. It checks:

- the required file tree and the executable bit of `scripts/install.sh`, `scripts/uninstall.sh` and `scripts/validate.sh` (in the working tree and in the Git index);
- skill and agent frontmatter, parsed as real YAML with PyYAML's safe loader (no arbitrary object construction): it must be a mapping without duplicate keys, with non-empty string `name` (matching the directory/file) and `description`, and no duplicate names. Definitions are taken from the full file inventory, so a missing, unreadable or unsafe (for example symlinked) `SKILL.md` or agent file fails;
- broken local Markdown links and references to installed files (`~/.claude/rules/...` is mapped to `claude/rules/...`, `~/.codex/skills/...` to `codex/skills/...`);
- hardcoded machine-specific home paths and accidental product/domain terms;
- sensitive paths in the Git index (reported by path only, even when ignored or deleted from the working tree), credential-like patterns in safe files (reported without the value), symlinks escaping the repository and files below a symlinked parent directory (reported, never read);
- unsafe prescriptions (permission or hook bypasses, destructive commands, privileged execution, protected-branch bypasses) that appear without a prohibition in the same sentence;
- obsolete delivery instructions in the installed `claude/` and `codex/` content that contradict `claude/rules/git-workflow.md`: requiring approval for every commit, or forbidding pushes even when delivery is authorized. Narrower restrictions are not reported: conditional wording (such as "without authorization" or "unless"), explicit user restrictions, prohibitions limited to force pushes or default/protected branches, and prohibitions aimed at Claude inside the `supervise-claude` skill;
- consistency of the commit emoji/type convention;
- Bash syntax of every `*.sh` file.

The file inventory comes from `git ls-files --cached --others --exclude-standard`, so it works on a fresh clone and on an uncommitted working tree. Paths that look like secrets are classified by name before anything is opened, checking every path component (so a matching directory covers everything below it): `.env*`, `credential*`, `secret*` and `token*` (also dot-prefixed, such as `.credentials.json`), `auth` and `auth.*` (such as `auth.json`, `auth.yaml`), private keys and key stores (`id_rsa*`, `id_ed25519*`, `*.pem`, `*.key`, `*.p12`, `*.pfx`, `*.ppk`, `*.jks`, `*.keystore`), credential files (`.netrc`, `.git-credentials`, `.pypirc`, `.npmrc`) and cloud/tool credential directories (`.aws`, `.azure`, `.gcloud`, `.kube`, `.docker`, `.oci`, `.ssh`, `.gnupg`, `.config/gcloud`, `.config/gh`). They are never read, hashed or copied, and their index content is never inspected; untracked ones are left untouched.

**Limitations:** the checks are heuristics. The unsafe-prescription and obsolete-delivery checks are sentence-based pattern matches, not semantic analysis: they can miss cleverly phrased instructions or accept a qualifier word used out of context. The credential scan covers common token formats only, and the sensitive-name list can flag harmless names. Passing validation is not a substitute for human semantic and security review.

The tests run the real scripts in subprocesses with a temporary `HOME` and temporary repositories; they never touch your actual home directory.

## Adding content

- **Rule:** add `claude/rules/<name>.md`, reference it from `claude/CLAUDE.md` when it should always apply, add it to `MANAGED_ITEMS` in `scripts/lib/common.sh` and to `REQUIRED_FILES` in `scripts/validate.py`, and extend the tests that enumerate managed links.
- **Skill:** add `claude/skills/<name>/SKILL.md` (or `codex/skills/<name>/SKILL.md`) with `name: <name>` and a `description` in the frontmatter; register the directory as above. Link the skill directory, never the whole `skills/` directory.
- **Agent:** add `claude/agents/<name>.md` with `name` and `description`; register it as above.
- Keep content general: no product names, business domains, stack-specific business rules or machine paths. Those belong in each project's own `.claude/`.

## Claude and Codex supervision

The Codex skill `supervise-claude` lets a Codex session plan a task, delegate the implementation to Claude Code, review the diff and tests independently (at most three implementation cycles), and deliver through logical commits, a task-branch push and a pull request left open for human review and merge. See [its SKILL.md](codex/skills/supervise-claude/SKILL.md).

## Commits and delivery

Commits follow the emoji Conventional Commits convention and the branch/PR flow in [claude/rules/git-workflow.md](claude/rules/git-workflow.md). Contribution rules are in [CONTRIBUTING.md](CONTRIBUTING.md).

## Scope and ownership

The repository owns only the eight linked items. It does not version or manage Claude/Codex settings, permissions, authentication, caches, plugins, histories, built-in or third-party skills, or project trust configuration; those stay machine-local and untouched.
