# Testing

Automated tests are part of implementation, not optional follow-up work. Follow the current project's testing conventions, tools and architecture; explicit project conflicts take precedence and must be explained. Do not introduce a new runner or framework just to apply these rules.

## Default completion rule

**EVERY NEW OR CHANGED MEANINGFUL EXECUTABLE BEHAVIOR MUST HAVE APPROPRIATE AUTOMATED TEST COVERAGE.** An implementation is not COMPLETE when that coverage is missing, unless an explicit, justified exception applies. Existing tests can satisfy the requirement if they actually exercise the changed behavior; identify that evidence instead of adding redundant tests.

## Test pyramid

Prefer many fast, focused tests at the bottom and fewer, slower, broader tests at the top. Choose the lowest level that proves the behavior with confidence.

| Change | Required coverage |
| --- | --- |
| Business/domain logic | Unit tests. |
| Services, use cases, state management | Unit tests for meaningful rules and branches. |
| Repositories, gateways, integration adapters | Unit tests for mapping/error logic; integration tests when interaction with the real boundary matters and is safely testable. |
| Database policies, functions, migrations | Integration tests against a local or disposable database, including applicable allowed and denied access. |
| UI components and widgets | Component/widget tests for non-trivial observable behavior, not framework internals. |
| Critical cross-layer flows | Integration or end-to-end tests when the risk justifies their cost. |
| Bug fixes | A regression test that fails before the fix and passes afterward whenever technically feasible. |

Use synthetic data and safe local/isolated environments. Tests do not authorize production access, real secrets, destructive resets or bypassing security controls. Do not undo user changes to demonstrate a failing regression test; use safe isolation or explain why the pre-fix run could not be performed.

## Scenarios and quality

Cover applicable happy paths, validation failures, error paths, boundaries/edge cases, authorization/access denial, important state transitions and regressions. Do not cover only success when meaningful failure behavior exists.

Tests must be deterministic, independent and clearly describe behavior. Avoid meaningless assertions, unnecessary coupling to implementation and excessive mocking. Mock external boundaries, not the business logic under test. Prefer observable behavior over tests that reproduce implementation details. Do not weaken or delete tests simply to make the suite pass.

## Coverage philosophy and exceptions

Do not chase 100% line coverage as an end in itself or game coverage metrics. Coverage percentage is an additional signal, never a substitute for good tests.

Generated code, trivial declarations, static declarative configuration and framework boilerplate may be excluded when tests add no meaningful value. Classify by behavior and risk, not file extension: executable configuration or security policies can require tests. Documentation-only edits generally need reference/format checks, not application builds.

When deciding not to add tests for changed executable code, explicitly state the affected behavior, why new tests add no meaningful value or are technically infeasible, any existing coverage or alternative evidence, and residual risk. Convenience or an unavailable runner is not an exemption from writing necessary tests. An exception to adding tests does not excuse required validation failures or uncertainty.

## Validation and reporting

After implementation, run the relevant tests when the environment safely allows it. Select commands from the project; record command, working directory and actual outcome. Never infer current success from a historical report or claim unexecuted tests passed.

- **COMPLETE:** required tests exist, required validations ran and passed.
- **PARTIAL:** a usable result with environmental validation limitations or remaining uncertainty.
- **BLOCKED:** unsafe to continue, or implementation problems prevent a usable result.

Do not report COMPLETE when mandatory tests are missing or failing, relevant tests were not written without a justified exception, or necessary validation could not run and its result remains uncertain. Distinguish preexisting failures from regressions without treating either as a green required check.

Map meaningful behavior changes to tests and results (or explicit exceptions) in the final review. Re-run affected checks after fixes; do not repeat a passed full suite without new changes or unresolved concerns.
