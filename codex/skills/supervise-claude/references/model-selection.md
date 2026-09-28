# Claude model selection

Read this before choosing the model for each Claude invocation. It details the model choice in step 4 of the [supervise-claude skill](../SKILL.md). The deterministic rules below are encoded in `scripts/supervision_policy.py`; run `python3 scripts/supervision_policy.py select-model --request <file.json>` from the skill directory before every cycle and pass the reported model to `scripts/run_claude.py --model`.

## Policy

- **Intended models.** The policy targets Claude Sonnet 5 (default) and Claude Opus 5.5 (deliberate escalation). It never hardcodes a full model ID; it is implemented portably through the installed CLI's own advertised stable aliases, `sonnet` and `opus`, so it keeps tracking whichever Sonnet/Opus generation the installed CLI currently maps those aliases to.
- **Default `sonnet`.** The initial implementation and ordinary correction cycles use the CLI's stable `sonnet` alias.
- **`opus` is deliberate and rare.** It is selected only in one of these cases, and always with a justification recorded in `state.md`:
  - the initial cycle, when the plan in `task.md` objectively documents the task as high-complexity or high-risk (`planning_assessment` contains `high-complexity` or `high-risk`);
  - the final allowed cycle (cycle 3), for a still-unresolved `semantic` or `correctness` finding that Codex review plausibly attributes to implementation reasoning difficulty (`reasoning_difficulty: true`), with no environment failure unresolved.
- **Never escalate** for ordinary corrections, task size alone, formatting, timeouts, authentication, quota, network, transport or permission failures, missing dependencies/SDKs or any other environment failure. Classify these as blockers or corrections as usual; a stronger model does not fix them.
- **Task-level overrides.** An explicit task instruction to use only one supported model (`override: "sonnet"` or `"opus"`) or to never escalate (`override: "no-escalation"`) is respected for every cycle. Any other requested model is refused: stop and report instead of substituting a different model.
- **Advertised aliases only.** `run_claude.py` passes `--model <alias>` only when the installed CLI advertises `--model` and the alias in its help. Otherwise it fails before invocation; never hardcode an unadvertised full model ID or silently fall back to another model.

## Model/implementation failure versus environment failure

Distinguish these before treating any failure as evidence about the model:

- **MODEL/IMPLEMENTATION FAILURE:** Claude ran, read the task and produced (or attempted) a change, but the result is wrong, incomplete or reasoned about poorly (an unresolved `semantic` or `correctness` finding). This is the only kind of failure `_escalation_blockers` treats as possible escalation evidence, and only on the final allowed cycle with `reasoning_difficulty: true`.
- **ENVIRONMENT FAILURE:** the cycle could not run Claude's reasoning at all, for a reason external to the model: authentication, quota, network, transport, timeout, permission-denied or a missing dependency/SDK (`ENVIRONMENT_FAILURES`), including a pre-implementation failure before Claude produced any change. Per the [cycle budget](../SKILL.md#cycle-budget), the counter is persisted before invoking Claude and an invocation that fails or times out still consumes that cycle; this bookkeeping rule is unrelated to model capability. An environment failure is never evidence that Sonnet is insufficient, and never justifies escalating to Opus: `select_model` refuses to escalate whenever any `ENVIRONMENT_FAILURES` category is unresolved, even alongside a genuine `semantic`/`correctness` finding.

## Invariants

The model changes nothing else. The three-cycle maximum, restricted `dontAsk` execution, tool allow/deny rules, sandbox and network controls, working-tree protection, Mechanical remediation and delivery ownership are identical for every model. Selecting a model never consumes, resets or extends the cycle budget.

## Request

```json
{
  "state": {"cycles_started": 2, "unresolved": ["correctness"], "validated": true},
  "model": {
    "override": "",
    "planning_assessment": [],
    "reasoning_difficulty": true,
    "justification": "Cycle 2 fix still violates the documented ordering invariant."
  }
}
```

`state.cycles_started` is the count before the cycle about to start. The command prints `{"model": ..., "reasons": [...]}`. It exits 2 for an unsupported override or an exhausted budget.

## Recording the cycle history and final report

Right after `start_claude_cycle` persists the incremented counter, run `record-cycle` with the already-incremented `state` (including any `cycle_records` recorded so far) and the `decision` from `select-model`:

```json
{
  "state": {"cycles_started": 3, "cycle_records": [{"cycle": 1, "model": "sonnet", "reason": "..."}, {"cycle": 2, "model": "sonnet", "reason": "..."}]},
  "decision": {"model": "opus", "reasons": ["escalation: Cycle 2 fix still violates the documented ordering invariant."]}
}
```

It prints the updated `{"cycle_records": [...]}` to persist in `state.md`; it exits 2 (never silently renumbers or overwrites) when the prior history is not itself a valid, sequential, fully-reasoned 1..N-1 record of supported models, when the cycle number does not directly follow the last recorded cycle, when the decision has no recorded reason, or when its model is unsupported. Once the run ends, render the concise section for `final-report.md` from the same `state`, including `cycles_started`:

```bash
python3 scripts/supervision_policy.py render-cycles --request <file.json>
# {"state": {"cycles_started": 2, "cycle_records": [{"cycle": 1, "model": "sonnet", "reason": "..."}, {"cycle": 2, "model": "sonnet", "reason": "..."}]}}
```

It prints a `Claude cycles:` line per recorded cycle (model and reason) followed by either `No escalation required.` or `Escalation occurred in cycle N: <reason>`. It exits 2, printing nothing, when the history is forged, malformed, out of order, records an unsupported model or a missing reason, or `cycles_started` does not exactly match the number of recorded cycles: rendering never trusts a history it has not itself validated.
