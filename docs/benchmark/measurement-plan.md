# Benchmark measurement plan

- Status: **draft, not pre-registered.** It becomes binding only with the pre-registration commit (#91), which also freezes the margins (OD-5), the tasks and the hidden tests before the first evaluation round.
- Issue: #89 (epic #26). Protocol: [ADR 0001](../decisions/0001-benchmark-protocol.md), which turns this plan into an executable harness; on any conflict, this plan wins and the ADR is corrected.
- Scope of this draft: §4.6 (statistical report) and §4.7 (data schemas, prices and retention), which `extract.py` (#90) and `report.py` (#30) implement. The other sections are added before pre-registration.

## Sections still to be added

These parts of the plan are **to be added before pre-registration** (#91):

- Every other section of the plan, including the ones ADR 0001 cites by number (for example plan §9 and §10): goal, hypotheses H1 to H4 in full, metrics O1, O2, K1 to K6, I1 to I8 and G1 to G5, threats to validity. Until then, the summaries in ADR 0001 apply.
- §4.2 task types: the vocabulary that `type` in `task.yaml` accepts (today `bugfix`, `feature`, `refactor`, `security`) and which types count as "small tasks" for H3.
- Margins: the numbers in §4.6 come from ADR 0001 (15 pp, 10 pp, ≤ 4× cost, ≤ 3× time, ≤ 50%) and stay open until OD-5 is decided in the pre-registration.
- Appendix A (`benchmark/project-claude.md`) and Appendix B (`benchmark/prompt-template.md`): the final text, identical in A, B and C (except the light-mode line that C appends to the prompt), that mentions nothing about the toolkit, asks the agent to end with `COMPLETE`, `PARTIAL` or `BLOCKED` on its last line (I8) and gives no hint of the hidden tests.

## §4.6 Statistical report

### Unit of analysis and eligible runs

- **The unit of analysis is the task.** Repetitions of a task are correlated, so treating them as independent would inflate confidence. For each task and condition, the success rate is the mean of O1 over its repetitions.
- **Intention to treat:** every run in which the agent started counts. Runs that end `timed_out`, `budget_capped`, `no_result`, `ended_blocked`, `empty_diff`, `workspace_invalid` or with an `evaluation_error` have O1 = 0, as the runner already records.
- **Excluded:** only runs with `status = error`, where the harness failed before the agent ran. The report lists each exclusion with its reason.
- Pilot tasks are left out of every conclusion (ADR 0001).

### Procedures

All procedures use the Python standard library only.

- **Success difference (H1, H3):** for each task, `d_t = rate_B,t − rate_A,t` (for H3, `rate_C,t − rate_B,t`). The estimate is the mean of `d_t`, in percentage points. Its 95% CI is a **percentile bootstrap that resamples tasks**: 10,000 resamples with `random.Random(seed)`, and the seed is written in the report.
- **Sign test:** an exact two-sided binomial test (`math.comb`) on the non-zero `d_t`. The report gives the number of ties.
- **Cost and time ratios (H2, H3):** per task, the mean cost (and the mean `duration_ms`) of each condition. The ratio is the **geometric mean** of `cost_B,t / cost_A,t` over tasks, with a bootstrap CI on the logs that resamples tasks.
  - Budget-capped runs enter with their observed cost and are flagged. Their count per condition is shown next to the ratio, because the cap truncates B more often than A.
  - A run without a `result` event (a timeout or the budget cap) has no `total_cost_usd` or `duration_ms` from the stream. It still enters: its cost is its transcripts' tokens × the price table, the same estimate as `cost_usd_est` (`cost_source = transcript` in `runs.csv`), and its time is the wall clock from start to stop (`duration_source = wall`). For a timed-out run, that is the time limit. A run with no transcript either (`cost_source = none`) is left out of the cost ratios and counted next to them; it still counts for success.
  - H2 has two parts, cost and time, reported separately. H2 as a whole is "evidence for" only when both parts are.
- **H4 security:** per security task, the number of hidden access-denied tests that failed in each condition. The report gives the paired difference B − A with a sign test. With the ~3 security tasks planned, H4 is **descriptive**.

### Tests and multiplicity

H1 is primary. The secondary family adjusted with Holm's method has four members: H2 cost, H2 time, H3 success and H3 cost. H4 is descriptive and outside the family; its p-value is reported unadjusted and labelled "descriptive".

| Member | Null hypothesis | Test, over tasks |
|---|---|---|
| H1 | the median of `d_t` (B − A) is 0 | sign test of `d_t` |
| H2 cost | the median of `log(cost_B,t / cost_A,t)` is `log 4` or more | one-sided sign test of `log ratio_t − log 4` |
| H2 time | the median of `log(time_B,t / time_A,t)` is `log 3` or more | one-sided sign test of `log ratio_t − log 3` |
| H3 success | the median of `d_t` (C − B) is −10 pp or less | one-sided sign test of `d_t + 0.10` |
| H3 cost | the median of `log(cost_C,t / cost_B,t)` is `log 0.5` or more | one-sided sign test of `log ratio_t − log 0.5` |

The verdicts below come from the **unadjusted** 95% CIs. Holm adjusts only the p-values that the report shows next to them, and a secondary verdict is labelled "secondary" either way.

### Verdict rules

The margins are OD-5's and stay open until the pre-registration. The labels are frozen with them.

| Hypothesis | Evidence for | Inconclusive | Evidence against |
|---|---|---|---|
| H1: B − A ≥ margin (15 pp) | CI lower bound > 0 **and** estimate ≥ margin | CI contains 0 | CI upper bound < 0 |
| H1, variant | CI lower bound > 0 and estimate < margin: "positive effect below the margin" | | |
| H2: cost ≤ 4×, time ≤ 3× | CI upper bound of the ratio ≤ margin | CI crosses the margin | CI lower bound > margin |
| H3: C vs B on small tasks | lower bound of `C − B` ≥ −10 pp **and** upper bound of `cost_C / cost_B` ≤ 0.5 | any CI crossing its margin | upper bound of `C − B` < −10 pp, or lower bound of the ratio > 0.5 |
| H4 | always descriptive, unless there are at least 8 security tasks | | |

"Evidence against" for H1 means evidence of harm. A CI that lies entirely between 0 and the margin is reported as "positive effect below the margin". A CI whose upper bound is below the margin but whose lower bound is ≤ 0 is reported as "inconclusive".

### Minimum sample and power

- The report labels **every** result "inconclusive by sample" when fewer than 8 tasks have at least one `finished` run in each of the three conditions (the ADR minimum), or when more than 20% of the planned runs end with `status = error`.
- Power, as an illustration with a **synthetic assumption**: if the standard deviation of `d_t` across tasks is ~0.35, then with 12 tasks the standard error is ~0.10 and the half-width of the 95% CI is ~20 pp. Only effects of about 20 pp or more have a fair chance of excluding zero, so **a true 15 pp effect will probably be "inconclusive"**.
- H3 uses only the small tasks (~6 of 12), so its expected verdict is "inconclusive". The report says so before the result, not after it.

### Rule for the number of tasks after the pilot

This rule is written before the pilot and applied once, right after it, before the pre-registration:

1. Compute `s`, the standard deviation of `d_t` (B − A) over the pilot tasks, as a proportion (0.35, not 35 pp).
2. Compute the number of tasks that gives 80% power for the 95% CI to exclude 0 when the true effect equals the H1 margin `m`, also as a proportion (0.15): `n = ceil(((1.96 + 0.84) × s / m)²)`.
3. If `n ≤ 12`, keep 12 evaluation tasks.
4. If `12 < n ≤ N_max`, raise the number of evaluation tasks to `n`. The extra tasks and their cost need the owner's authorization, like any round.
5. If `n > N_max`, keep the planned number of tasks and write in the pre-registration that H1 is expected to be inconclusive, unless the owner revises the margin (OD-5) before the pre-registration.

Consequences, worked out:
- With `m = 0.15`, keeping 12 tasks requires `s ≤ 0.15 × √12 / 2.8 ≈ 0.19`. With `s = 0.25` the rule asks for 22 tasks; with `s = 0.35`, for 43.
- Four pilot tasks estimate `s` poorly, so the decision is uncertain in both directions.
- Power to exclude 0 is not power to reach "evidence for": at a true effect of exactly `m`, the estimate reaches the margin only about half of the time. The rest mostly lands in "positive effect below the margin".

`N_max` (the most tasks the owner can author and pay for) and the 80% power target are set by the owner in the pre-registration. `s`, `n` and the decision are recorded in the pre-registration commit.

### Report structure (Markdown)

1. Header: round, dates (day only), full model ID, effort, Claude Code version, `toolkit_sha`, `harness_sha`, seed, number of tasks and runs, and the exclusions with their reasons.
2. Verdict table for H1 to H4: estimate, 95% CI, sign-test p-value (Holm-adjusted for secondary hypotheses) and label.
3. Success per task and condition: paired points, as text or a table.
4. Cost, tokens (all 4 types), time and turns per condition: median, P25 to P75, and the ratios with their CIs.
5. Cost per role, from `role_costs.csv`, with the ±5% reconciliation (§4.7).
6. Guardrails per condition: % `protected_changed` (G1), broken suite or build, `timed_out`, `budget_capped`, `empty_diff` and `ended_blocked`.
7. Descriptive results per task type, without inference.
8. Limitations: the fixed text of "Known limitations" in ADR 0001, plus the ones specific to the round.

A **synthetic** row of the verdict table (the format, not a result):

```text
| H1 | B − A success | +X pp | 95% CI [L; U] | sign p = P (Holm n/a) | inconclusive | 12 tasks, 108 runs |
```

## §4.7 Data schemas, prices and retention

### Common rules

- Format: UTF-8, comma-separated, with a mandatory header row.
- Values: booleans are `true` and `false`, null is an empty field, and numbers use a decimal point.
- **No free-text column.** Every text value is an ID or a value from a closed vocabulary, validated by a regular expression as `extract.py` already does.
- A missing column or an empty file is a clear error (#73, criterion 26).

### `runs.csv` (one row per run)

| Column | Type | Source |
|---|---|---|
| `round_id`, `run_id` | ID | runner |
| `round` | `smoke`, `pilot` or `evaluation` | `meta.json` (`round`), so pilot runs can be left out without parsing `round_id` |
| `task_id`, `task_type`, `stack` | ID, §4.2 vocabulary, ID | `task.yaml` |
| `condition` | `A`, `B` or `C` | plan |
| `repetition`, `order_index`, `seed` | int | plan (`order_index` from `meta.json` `order`) |
| `run_date` | ISO date (day) | the day of `meta.json` `started_at` |
| `model_id`, `claude_code_version`, `toolkit_sha` | str | `model_id` from `meta.json` `model`; `claude_code_version` is the version pinned in the image (#29), which #90 adds to `meta.json`; `toolkit_sha` is empty in A |
| `status` | `finished` or `error` | runner |
| `o1_success` | bool | evaluator |
| `hidden_passed`, `hidden_total` | int | `eval.json` |
| `hidden_access_denied_failed` | int | `eval.json` and the task's access-denied criteria (H4); `task.yaml` has no field that marks them yet, a schema dependency on #28 |
| `suite_passed`, `build_passed` | bool or empty | `eval.json` |
| `protected_changed`, `no_result`, `ended_blocked`, `empty_diff`, `workspace_invalid` | bool | `meta.json` (`protected_changed` is true when its list of paths is not empty) |
| `evaluation_error` | vocabulary or empty | `meta.json` |
| `total_cost_usd` | float | K1, from the `result` event; without one, the transcripts' tokens × the price table (§4.6) |
| `cost_source` | `result`, `transcript` or `none` | where `total_cost_usd` came from |
| `input_tokens`, `output_tokens`, `cache_creation_tokens`, `cache_read_tokens` | int | K2 (`usage`) |
| `duration_ms` | int | K4, from the `result` event; without one, the wall clock from start to stop |
| `duration_source` | `result` or `wall` | where `duration_ms` came from |
| `num_turns` | int | K5 |
| `files_changed`, `lines_added`, `lines_deleted` | int | I4 to I6, from `final.patch` |
| `expected_files_touched`, `expected_files_total`, `unexpected_files_touched` | int | I7, from `final.patch` and `expected_files` |
| `findings_critical`, `findings_high`, `findings_medium`, `findings_low` | int | I1 to I3 (heuristic, validated in the pilot) |
| `edits_after_report` | int | I2 and I3 (rework) |
| `harness_sha`, `effort`, `permission_profile`, `exit_code`, `result_subtype`, `timed_out`, `budget_capped`, `subagent_calls`, `committed` | ADR 0001 extras | runner and `extract.py`; `subagent_calls` is the total count (int), and the count per role is in `role_costs.csv` |

### `role_costs.csv` (one row per run and role)

| Column | Type | Note |
|---|---|---|
| `round_id`, `run_id`, `task_id`, `condition` | ID | join with `runs.csv` |
| `role` | role taxonomy | `main`, the 18 agent names of [the team](../team.md), or `other`; an unknown `subagent_type` becomes `other` |
| `invocations` | int | |
| `input_tokens`, `output_tokens`, `cache_creation_tokens`, `cache_read_tokens` | int | from the transcripts |
| `cost_usd_est` | float | tokens × the price table |
| `duration_ms` | int or empty | sum of the intervals between the subagent's consecutive events, leaving out intervals longer than an idle cap (300 s proposed, as in the process metrics, #76) |
| `price_table_version` | ID | |
| `source` | `transcript`, `stream` or `none` | `none` when the transcript is missing; the row still exists, with nulls |

A **synthetic** row:

```csv
round_id,run_id,task_id,condition,role,invocations,input_tokens,output_tokens,cache_creation_tokens,cache_read_tokens,cost_usd_est,duration_ms,price_table_version,source
pilot-1,r0007,T02,B,reviewer,1,12000,1800,9000,240000,0.41,95000,p2026-11,transcript
```

### Price table and reconciliation

- **Cost per role** is the token counts multiplied by a **versioned price table**, `benchmark/prices.csv`, with one row per model and version:
  - `price_table_version` and `model_id`;
  - `effective_date`;
  - the USD prices per million `input`, `output`, `cache_creation` and `cache_read` tokens;
  - `source`.

  Prices come from the owner or from a cited public source and are never invented. A change of prices adds a new version instead of editing an old one.
- **Reconciliation:** for each run, the report compares the sum of `cost_usd_est` over its roles with `total_cost_usd` in `runs.csv` and shows the difference in percent. As a cross-check, it also splits `total_cost_usd` across roles in proportion to their price-weighted tokens.
  - Runs with any `source = none` role, or with `cost_source` other than `result`, are left out of the reconciliation and counted.
- **Pilot gate (this plan's, referred to by ADR 0001):** the pilot passes when the difference between the sums over all reconciled pilot runs is within **±5%**. Otherwise, cost per role is presented only as descriptive. The per-run differences are always shown.
- **OpenTelemetry** is used only as a cross-check in the pilot, never as the primary source.

### Retention

- Raw logs and transcripts (`RUN_ROOT`) stay local, are never versioned, and are deleted within **90 days** of the round. This period is pending the privacy review.
- The per-run outputs in `results/` (`final.patch`, `meta.json`, `eval.json`) also stay local and unversioned. They are kept until the round's blind review and report are done, then deleted under the same rule.
- Only the numbers in `runs.csv` and `role_costs.csv` are kept for good, and those files contain no free text.

### The `process-metrics` instrument in the benchmark

Conditions B and C run **without** the `process-metrics` instrument (#77). Findings I1 to I3 come from the heuristic in every condition. Running B with the instrument would add a prompt difference between A and B that is not the treatment under test.

Because the instrument from #77 is installed for every user and asks for a mode on the first task of a project, the harness has to keep it out of B and C. It must not do this through `project-claude.md`, which stays identical in all conditions and mentions nothing about the toolkit (Appendix A). The mechanism is defined in a follow-up issue and checked in the smoke round.

## Open points for the pre-registration

The owner decides these before the pre-registration (#91):

- `N_max` and the 80% power target of the task-count rule (§4.6).
- Whether to add an "evidence against the margin" label for H1 (CI upper bound below 15 pp). Today that case is "inconclusive" or "positive effect below the margin".
- Whether an `evaluation_error` caused by the evaluator itself (a harness fault, not the agent's) keeps O1 = 0 under intention to treat, or moves to the exclusions with `status = error`.
- The idle cap used for `duration_ms` in `role_costs.csv`.
