---
name: e2e-testing
description: Guidance for writing and running automated acceptance and end-to-end tests derived from acceptance criteria in any repository — choosing the level, writing stable tests against user-visible behavior, test data and isolation, flakiness and local-only execution with the project's existing tooling (for example Playwright, Cypress, Flutter integration_test or API tests). Use when QA or a developer adds tests that prove acceptance criteria across components, screens or services.
---

# Acceptance and end-to-end testing

The levels and coverage rules are in the `testing` rule; stack details are in the stack skills (`flutter-dev`, `web-dev`, `supabase-dev`, `java-spring-dev`). This skill adds how to turn acceptance criteria into stable tests above the unit level.

## Choose the level

- Map each acceptance criterion to the lowest level that proves it as a user or client sees it: an API or integration test when the rule lives on the server, a component or widget test of the whole screen when the behavior is in one client, and an end-to-end test only for critical journeys that cross several of them.
- Keep end-to-end tests few: they are the slowest and most fragile. One journey test per critical flow, with the variations covered at lower levels.
- Use the tooling the project already has. When a needed level has none, recommend it with its cost instead of adding a framework.

## Write stable tests

- Name each test after the criterion it proves, and keep one behavior per test.
- Drive the interface the way a user does and find elements by role, label, text or test identifiers, never by CSS paths, widget tree positions or generated class names.
- Wait for conditions (an element visible, a request finished), never for fixed times.
- Assert on outcomes the user can observe (text, navigation, saved data read back through the interface or API), not on internals.
- Cover the negative paths the criteria name: validation errors, denied access for another user or an anonymous visitor, and failure messages.

## Data and isolation

- Each test creates the data it needs with synthetic values and does not depend on other tests or on their order.
- Run against a local or disposable environment (local server, local database, emulator, mocked external services); never against production or shared remote environments, and never with real accounts or personal data.
- Clean up through the same interfaces, or rely on a disposable environment the project documents as such; never reset a database that is not documented as disposable.

## Flakiness

- A flaky test is a defect: find the cause (race, shared state, real time, network, animation) and fix it. Do not add retries or longer timeouts to hide it, and do not delete or skip it to make the suite pass.
- Fix the clock, time zone, random seeds and locale when the behavior depends on them.

## Run and report

- Use the project's commands in a non-interactive, headless mode, keeping traces, screenshots or logs on failure in the project's configured artifact location (never committed) or a temporary location.
- Report each criterion with its test and result. A test you could not run (missing browser, emulator, container or service) is BLOCKED, not passing, and a new test that fails against the change is a finding with the failing step and excerpt.
