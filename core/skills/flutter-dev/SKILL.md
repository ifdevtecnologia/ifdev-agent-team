---
name: flutter-dev
description: Stack guidance for implementing, testing or reviewing a change in a Flutter/Dart app (mobile, desktop or web) — project structure, state, widgets, platform permissions, offline behavior, accessibility, store policies and the Flutter test levels. Use when a change touches Dart code, widgets, pubspec dependencies or Android/iOS platform files.
---

# Flutter development

Apply this on top of the project's own instructions, which win when they are more specific: its `CLAUDE.md`, `AGENTS.md`, `.claude/rules/`, `analysis_options.yaml` and the patterns already in the codebase. General rules stay in `~/.claude/rules/engineering-principles.md` and `~/.claude/rules/testing.md`; this skill adds only what is specific to Flutter.

## Before writing code

- Confirm the Flutter and Dart versions, packages and commands in `pubspec.yaml`, `pubspec.lock`, version files (for example `.fvmrc`) and the CI configuration, not from memory.
- Find the project's state management, navigation, dependency injection and error handling approach and follow it. Do not introduce a second one.
- Search for existing widgets, theme tokens, extensions and helpers before writing new ones. Reuse the design system (theme, spacing, typography) instead of hard-coded colors and sizes.

## Writing code

- Keep business rules out of widgets: widgets render state and forward intents; rules live in plain Dart classes that can be unit tested without Flutter.
- Prefer small, composable widgets and `const` constructors. Extract a widget when a build method mixes concerns or grows hard to read, not to satisfy a line count.
- Handle every async state a user can see: loading, empty, error, success and stale data. Never leave a `Future` unawaited without a reason, and check `mounted` (or the context) after an `await` before using the context.
- Dispose controllers, streams and subscriptions you create.
- Offline and failure: decide what happens without network, on timeouts and on server errors; keep the user's input when a request fails.
- Text and layout: no fixed heights for text, support large font scales and small screens, and avoid overflow.
- Accessibility: semantic labels for icons and images, touch targets of at least 48 dp, focus order that matches reading order, contrast from the theme, and reduced motion respected for animations.
- Strings visible to users go through the project's localization mechanism when it has one.

## Platform, privacy and stores

- Request a native permission only when a feature needs it, at the moment of use, with the platform's usage description and a path when the user denies it. Remove permissions a change no longer needs.
- Keep tokens and sensitive values in the platform's secure storage, never in plain preferences, logs or crash reports. Review Android backup and iOS file protection settings when the app stores sensitive data.
- Adding a package: check maintenance, platform support, license, required permissions and network or analytics behavior; prefer the SDK or an existing dependency when it is enough. Flag a new or upgraded dependency to the main agent for the security review.
- Store policies (data safety forms, privacy labels, children's and health rules) can change what a feature may do; flag the impact instead of assuming it is allowed.

## Tests

- Unit tests for business logic and state classes, with no widgets involved.
- Widget tests for non-trivial observable behavior: rendered states, interactions, validation messages and accessibility semantics. Find widgets by semantics, keys or text, not by widget tree structure.
- Integration tests (`integration_test`) for critical flows across screens when the project keeps them and the risk justifies them.
- Golden tests only when the project already uses them.
- The boundaries to fake are HTTP, storage, platform channels and the clock.

## Verify

Use the project's commands, typically formatting in check mode, `flutter analyze` (or `dart analyze`) and `flutter test`, plus a build of each affected platform when it can run locally. Platform builds that need signing, store credentials or a device you do not have are BLOCKED, not passing.
