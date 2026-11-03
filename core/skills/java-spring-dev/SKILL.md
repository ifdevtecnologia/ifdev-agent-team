---
name: java-spring-dev
description: Stack guidance for implementing, testing or reviewing a change in a Java service built with Spring Boot — layering, dependency injection, REST controllers and validation, transactions and persistence with JPA, configuration and secrets, error handling, security and the Java test levels. Use when a change touches Java sources, Spring configuration, Maven or Gradle build files, or database access in a Spring application.
---

# Java and Spring Boot development

Apply this on top of what the project defines, which wins when it is more specific: the project's instructions, build configuration, static analysis setup and the patterns already in the codebase. General rules stay in the `engineering-principles` and `testing` rules; this skill adds only what is specific to Java and Spring.

## Before writing code

- Confirm the Java, Spring Boot and dependency versions and the build commands in `pom.xml` or `build.gradle(.kts)`, the wrapper, version files and the CI configuration, not from memory.
- Find the project's layering (for example controller, service, repository, or a hexagonal or modular layout), its mapping approach, exception handling and test setup, and follow them.
- Search for existing services, mappers, validators, utilities and base classes before writing new ones.

## Writing code

- Constructor injection with `final` fields; no field injection. Keep components small and focused; a service with many unrelated dependencies is a sign of mixed responsibilities.
- Keep business rules in the domain or service layer, free of web and persistence details. Controllers translate HTTP to calls and back; repositories only persist.
- Use DTOs (records are a good fit) at the API boundary; do not expose entities directly. Validate input with Bean Validation (`@Valid` and constraints) at the boundary and re-check business invariants in the domain.
- Errors: domain exceptions mapped once in a `@RestControllerAdvice` to consistent responses (for example `ProblemDetail`), without stack traces or internal details for clients. Never swallow exceptions.
- Transactions: `@Transactional` on service methods that need atomicity, read-only where possible, never on controllers. Self-invocation bypasses the proxy, and by default only unchecked exceptions roll back.
- Persistence: avoid N+1 queries (fetch joins, entity graphs or projections; `spring.jpa.open-in-view` left on hides them), paginate unbounded lists, base entity equality on a business key or handle the id that is null before persisting, and evolve the schema only through the project's migration tool (Flyway or Liquibase), one migration per logical change, never editing one already applied.
- Prefer immutability, `Optional` as a return type only, and the standard library (streams, `java.time`) over custom helpers. Avoid static mutable state.
- Configuration through typed `@ConfigurationProperties`; secrets only from environment or the secret store, never in `application*.yml` committed to the repository or in logs.

## Security

- Authorization is enforced on the server for every endpoint and every resource owner (method security or explicit checks), not inferred from the client.
- Use parameterized queries or the repository abstraction; never build SQL or JPQL from user input.
- Review CORS, CSRF and actuator exposure when touching security configuration; changes to authentication, authorization or dependencies are flagged to the main agent for the security review.

## Tests

- Unit tests (JUnit 5, with AssertJ and Mockito when the project uses them) for domain and service logic, mocking only the boundaries.
- Slice tests for a layer: `@WebMvcTest` for controllers (validation, status codes, error mapping), `@DataJpaTest` for repositories and queries, against the real database engine (Testcontainers with `@AutoConfigureTestDatabase(replace = NONE)`) when the project uses it, since the default embedded database can hide engine-specific behavior.
- Integration tests with `@SpringBootTest` and a real database in a container (Testcontainers) when the project uses them, for flows across layers and for migrations.
- No tests against shared or remote environments; synthetic data only.

## Verify

Use the project's build commands, typically the Maven or Gradle `verify`/`check` task, which runs compilation, tests and the configured static analysis. Tests that need Docker without Docker available are BLOCKED, not passing.
