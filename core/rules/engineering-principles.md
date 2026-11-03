# Engineering principles

Apply these principles across projects as pragmatic heuristics, not dogmas. When an explicit conflict exists, follow the current project's conventions and architecture and explain the relevant tradeoff. Prefer simple, readable, testable code with low complexity. Do not impose a language, framework, domain model or spoken language on a project.

## Clean Code

- Use names that reveal intent; avoid cryptic abbreviations.
- Functions/methods should do one thing at one level of abstraction. Keep classes and modules small and cohesive, with intentional, minimal public APIs.
- Comments explain WHY, not WHAT the code already says.
- Do not leave dead code, empty catches, or unexplained magic numbers/strings.
- Avoid boolean flags that significantly change behavior; prefer explicit operations or useful abstractions.
- Prefer explicitness over cleverness.

## Coupling and cohesion

- Aim for low coupling and high cohesion: group what changes together, separate what changes for different reasons.
- Depend on stable contracts at module boundaries; keep infrastructure details out of business logic.
- Keep dependencies flowing in one direction; avoid cycles between modules.

## KISS and DRY

Choose the simplest solution that correctly solves the current problem. Do not introduce speculative abstractions, unnecessary frameworks, premature generalization, architecture for hypothetical requirements, or infrastructure before it is needed.

Avoid duplicated business logic. Extract shared behavior when duplication represents the same concept; do not deduplicate incidental similarity if it couples unrelated concepts.

## Reuse and simplicity

- **Search before writing:** look for existing functions, components, utilities, types and patterns in the project, then in the language, framework and current dependencies. Reuse or extend them before writing new code; do not add a dependency for what they already do.
- **Extract when it pays off:** create an abstraction when a concept repeats (often at the third occurrence) or when it isolates a real boundary or variation, not in anticipation of one.
- **Simplify before finishing:** once the tests pass, reread the change and remove what it does not need: unused parameters and options, single-use indirection, wrappers that only forward calls, defensive code for states that cannot happen and comments that restate the code.
- **Fewer moving parts:** prefer plain functions and data over classes when there is no state or polymorphism, standard library and framework features over custom machinery, and one clear path over configurable generality nobody asked for.
- Simple is not the same as short: do not trade readability, error handling or type safety for fewer lines.

## SOLID

- **Single Responsibility:** a class, module or function has one clear reason to change.
- **Open/Closed:** when variation is real, prefer extension through composition, strategy, registry or polymorphism over continuously growing conditional structures.
- **Liskov Substitution:** subtypes and implementations honor their contracts, including error and behavioral expectations.
- **Interface Segregation:** use small focused interfaces; do not force implementations to depend on methods they do not need.
- **Dependency Inversion:** domain/business code depends on abstractions or stable contracts rather than infrastructure details.

Do not create an interface merely to satisfy SOLID for a single trivial implementation without a useful boundary.

## Object Calisthenics

Use these as prompts for clearer design, never mechanical refactoring targets:

- Prefer one level of indentation per method/function; use guards and early returns over deep nesting.
- Avoid `else` when an early return or an appropriate polymorphic solution is clearer.
- Encapsulate a collection when the collection itself carries business rules.
- Avoid long train-wreck call chains in domain/business code.
- Keep functions/classes small and cohesive; avoid unnecessary getters/setters on domain objects.
- Prefer behavior-rich domain objects where appropriate.
- Introduce value objects when primitives represent meaningful domain concepts and the added structure improves correctness.

Exceptions include framework DTOs, serialization models, ORM/platform requirements, builders/fluent APIs, generated code, and any case where compliance makes the code less clear. Never refactor mechanically just to satisfy a rule.

## Design patterns

Use a pattern only to solve an actual design problem, not because its name fits:

- Strategy for replaceable business behavior.
- Factory when object creation has meaningful rules.
- Adapter for external integrations.
- Repository for persistence boundaries when useful.
- Command/use-case for business operations.
- Observer/events for decoupled reactions.
- State when behavior truly changes by state.
- Decorator for composable cross-cutting behavior.

Prefer composition over inheritance. Avoid pattern-heavy architecture, needless factories, unnecessary abstractions and interfaces without a useful boundary.

## Complexity and code quality

Use cognitive complexity **15** as a warning threshold for a function/method, not an absolute gate or a reason to game metrics. Use an available project analyzer when appropriate; do not invent a measured score. As complexity grows, extract meaningful methods, replace nesting with guards, isolate policies and use strategy/polymorphism only for real variation. Avoid god classes/modules and feature envy.

- Do not introduce deprecated APIs when supported alternatives exist.
- Do not introduce known code smells without an explicit reason.
- Handle errors deliberately and preserve type safety; never swallow errors silently.
- Prefer immutable data where practical; make invalid states difficult to represent where economically reasonable.
- Encapsulate state and invariants; expose behavior rather than internals.
- Validate inputs at appropriate boundaries.
- Do not sacrifice readability for micro-optimizations without evidence.

## Self-review

Explicitly check correctness, coverage of new/changed meaningful behavior (see the `testing` rule), missed reuse of existing code, SOLID/KISS/DRY, coupling and cohesion, unnecessary complexity, overengineering, security, error handling, regression risk, architecture boundaries, duplication, naming, dead code, magic values and deprecated APIs.

Do not blindly refactor unrelated existing code because it violates these principles. Improve adjacent code only when necessary to implement the current change safely. Report material findings and justified exceptions rather than manufacturing issues or repeating an empty checklist.
