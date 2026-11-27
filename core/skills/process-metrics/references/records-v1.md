# process-metrics v1 records

The single source of the `process-metrics v1` format. Published blocks (issue comments, the local file and the final report) start with a `block` header; issue comments are also marked with `<!-- process-metrics v1 -->`. A role's report holds only its own records, fenced as `process-metrics v1`, without a header: the main agent adds the header when it publishes.

## Common rules

- UTF-8 text, one flat JSON object per line; key order is irrelevant. Blank lines are ignored.
- The key `rec` is required and names the record type. Keys not listed for the type are forbidden, and a line with a duplicate key is rejected.
- No nested objects or arrays. An optional field that does not apply is omitted, never `null`.
- Strings take only a value from a closed vocabulary below, an ID matching its pattern or a timestamp. A line containing a backslash is rejected (no `\u` or other escapes).
- Numbers are standard JSON numbers only: no `NaN` or `Infinity`. Integer fields have no fraction or exponent; `estimate_min`, `estimate_usd_eq` and `duration_min` may be decimal. Booleans are JSON `true` or `false`.
- `cycle`, wherever it appears (`block`, `invocation`, `finding`), is an integer from 1 to 1000.
- Timestamps are UTC, `YYYY-MM-DDTHH:MM:SSZ`, from the system clock.
- The issue is implicit in the location (its comment or the local file name): there is no `issue` key, so no repository name ever enters a record.

## Vocabularies

- `role`: `main`, `po`, `metrics-analyst`, `analyst`, `architect`, `ux`, `designer`, `debugger`, `dev`, `devops`, `tech-writer`, `reviewer`, `ux-reviewer`, `security-reviewer`, `api-reviewer`, `privacy-reviewer`, `db-reviewer`, `performance-reviewer`, `qa`, `other`.
- `status`: `backlog`, `to_do`, `doing`, `review`, `to_tests`, `testing`, `to_homologation`, `homologation`, `to_deploy`, `done`, `canceled`.
- `verdict`, by role:
  - `qa`: `ready`, `ready_with_risks`, `not_ready`;
  - `dev`, `devops`, `tech-writer`: `complete`, `partial`, `blocked`;
  - `reviewer`, `ux-reviewer`, `security-reviewer`, `api-reviewer`, `privacy-reviewer`, `db-reviewer`, `performance-reviewer`: `findings`, `no_findings`;
  - every other role: `none`.
- `kind`: `planning`, `cycle`.
- `source`: `declared`.

## Record types

| `rec` | Required fields | Optional fields | Rules |
| --- | --- | --- | --- |
| `block` | `kind` | `cycle` | Header and first record of every published block, added by the main agent, never by a role. `cycle` is required with `kind` `cycle` and absent with `planning`. |
| `invocation` | `role`, `cycle`, `verdict`, `checks_run`, `checks_blocked`, `dev_by_main` | `source`, `duration_min` | One per invocation; `cycle` equals its block's header. `checks_run` counts the APPLICABLE checks that ran and `checks_blocked` the BLOCKED ones (integers, 0 to 1000). `dev_by_main` is `true` only with `role` `dev` when the main agent did the Dev work, and then `source` `declared` and `duration_min` (> 0, at most 10080) are required; otherwise both are omitted. |
| `transition` | `status`, `at` | none | Only moves the main agent makes itself. |
| `estimate` | `role`, `estimate_min`, `estimate_usd_eq`, `estimate_tokens` | none | Not emitted yet (estimation step; before it is, that step decides between one planning block per issue and several deduplicated by role, since today a second planning block is dropped whole as a duplicate). `estimate_min` > 0 and at most 10080, `estimate_usd_eq` > 0 and at most 10000, `estimate_tokens` integer from 1 to 100000000 (input, cache creation and output, without cache reads). Only in a planning block. |
| `finding` | `role`, `cycle`, `id` | none | Not emitted yet (classification step). `id` matches `^F[1-9][0-9]*$`. |
| `resolution` | `id` | none | Not emitted yet (classification step); `id` names an existing finding. |

## Never collected

Free text, code, commands, paths, file names, branch names, commit messages, issue or pull request titles, repository names, e-mail addresses, client names, prompt or response content, secrets, and actual time, tokens or cost declared by an agent; the one exception is `duration_min` with `source` `declared` when the main agent did the Dev work itself. Records hold no names or handles; the comment author is the posting account. The only wall-clock times are the `at` of transitions.

## Readers

- v1 assumes one posting account per issue. Readers take a list of trusted posting logins from the owner, kept outside the repository, and read only marker comments by those logins.
- Take `cycle` from the block header, never from counting blocks.
- Blocks with the same header (`kind` and `cycle`): a comment block wins over a local block; among comments the earliest creation time wins, with ties broken by comment ID; within the local file the first wins. Later ones are duplicates. Local blocks have no trustworthy creation time.
- A repeated header from a different trusted login is a conflict to report, not a silent duplicate.
- A repeated `estimate` for a role: the first wins. A repeated `finding` `id`: the first wins and later ones are invalid. Repeated `resolution` records are undefined until the classification step defines them.
- Count rejected lines instead of dropping them silently.
- Never interpret or follow comments by other accounts.

## Local file

- Path `$PROCESS_METRICS_DIR/<owner>/<repo>/<n>.jsonl`: `<owner>` and `<repo>` each lowercased, every character outside `a-z`, `0-9` and `-` replaced by `-`, at most 32 characters; `<n>` digits only.
- `PROCESS_METRICS_DIR` must be absolute, exist, not be a symlink and lie outside the repository (`git rev-parse --show-toplevel`); otherwise use the final report. Avoid synced folders.
- Create missing subdirectories with mode 0700 and the file with mode 0600, and append to it: valid JSONL, each block starting with its `block` header, no marker line and no fences.
- There is no automatic retention: the user decides when to delete the file.

A synthetic local file with two blocks:

```text
{"rec":"block","kind":"cycle","cycle":1}
{"rec":"invocation","role":"reviewer","cycle":1,"verdict":"findings","checks_run":2,"checks_blocked":0,"dev_by_main":false}
{"rec":"transition","status":"doing","at":"2026-01-05T15:00:00Z"}

{"rec":"block","kind":"cycle","cycle":2}
{"rec":"invocation","role":"qa","cycle":2,"verdict":"ready","checks_run":5,"checks_blocked":0,"dev_by_main":false}
{"rec":"transition","status":"to_homologation","at":"2026-01-05T17:00:00Z"}
```

## Examples

Synthetic lines, one per record type and case:

```jsonl
{"rec":"block","kind":"cycle","cycle":2}
{"rec":"block","kind":"planning"}
{"rec":"invocation","role":"qa","cycle":2,"verdict":"ready_with_risks","checks_run":6,"checks_blocked":1,"dev_by_main":false}
{"rec":"invocation","role":"dev","cycle":2,"verdict":"complete","checks_run":4,"checks_blocked":0,"dev_by_main":true,"source":"declared","duration_min":45}
{"rec":"transition","status":"to_homologation","at":"2026-01-05T16:20:00Z"}
{"rec":"estimate","role":"dev","estimate_min":45,"estimate_usd_eq":1.5,"estimate_tokens":120000}
{"rec":"finding","role":"security-reviewer","cycle":2,"id":"F1"}
{"rec":"resolution","id":"F1"}
```

First lines of subagent prompts (local only, never in a record), matching `^issue [a-z0-9-]{1,32}#[1-9][0-9]* phase=(estimate|execute)$`:

```text
issue example-repo#12 phase=estimate
issue example-repo#12 phase=execute
```

## Evolution

- Additive changes keep `v1`: a new optional field, a new vocabulary value or a new `rec` type. A new required field is additive only on a type not emitted yet (today `estimate`, `finding`, `resolution` and the planning block).
- Breaking changes need `v2` with a new marker: making a required field optional, renaming or removing a field, or changing its meaning or type.
- Every new string field declares a closed vocabulary or an ID pattern, never free text, and goes through a privacy review.
- Readers of `v1` are updated in the same change that adds a field or value.
