---
name: web-dev
description: Stack guidance for implementing, testing or reviewing a change in a web front end (Angular, Astro or another framework with TypeScript) — components, state, routing, forms, accessibility, performance, security headers and the web test levels. Use when a change touches components, pages, styles, client-side TypeScript or front-end build configuration.
---

# Web front-end development

Apply this on top of the project's own instructions, which win when they are more specific: its `CLAUDE.md`, `AGENTS.md`, `.claude/rules/`, lint and formatter configuration and the patterns already in the codebase. General rules stay in `~/.claude/rules/engineering-principles.md` and `~/.claude/rules/testing.md`; this skill adds only what is specific to the web.

## Before writing code

- Confirm the framework, Node and package manager versions and the scripts in `package.json`, the lockfile, version files and the CI configuration, not from memory.
- Find the project's component structure, state approach, routing, styling (tokens, CSS methodology) and data access, and follow them. Do not introduce a second state library, styling system or HTTP layer.
- Search for existing components, utilities, pipes, hooks and styles before writing new ones; extend the design system instead of copying it.

## Writing code

- Keep business rules out of templates and components: components render state and forward events; rules live in plain TypeScript that can be unit tested without the DOM.
- Use the type system: no `any` without a reason, typed API responses validated at the boundary, and states that cannot represent impossible combinations.
- Handle every state a user can see: loading, empty, error, success and partial data. Keep the user's input when a request fails.
- Forms: validate on the client for feedback and on the server for trust; show errors next to their fields and announce them to assistive technology.
- Accessibility: semantic HTML first (buttons, links, headings, landmarks, labels), ARIA only to fill gaps, full keyboard operation with visible focus, contrast from the design tokens, text alternatives, and reduced motion respected.
- Resilient layout: no horizontal scroll, text that can grow, and content that still works when scripts are slow.
- Performance: ship as little JavaScript as the page needs, lazy-load routes and heavy components, size and lazy-load images, and avoid layout shifts. Measure before optimizing.

## Mobile-first

Websites and web applications are mobile-first unless the project documents otherwise:

- Design and build the phone layout first (about 360–390 CSS px wide), still usable at 320 CSS px with no horizontal scroll (WCAG reflow); larger screens add to it, they do not take away from a desktop layout.
- In CSS the base rules are the phone rules, and wider layouts are added with `min-width` media or container queries. Use `max-width` queries only for a narrow exception, never as the main structure.
- Content and primary actions come first in the document order and stay reachable on the phone; navigation collapses into an accessible menu instead of being hidden.
- Touch first: targets of at least 44×44 CSS px (WCAG's 24×24 is the floor; a project that chooses less documents why), no behavior that depends on hover, and inputs with the right `type`, `inputmode` and `autocomplete` for on-screen keyboards.
- Keep the phone fast: images sized for the viewport (`srcset`, `sizes`), lazy-loaded below the fold, and no heavy script before the first content.
- Verify at phone width first, then at tablet and desktop widths, including the menu, forms and dialogs.

## Framework notes

- **Angular:** follow the project's choice between standalone components and modules, and between signals, RxJS and stores. Unsubscribe or use the async pipe or `takeUntilDestroyed`; prefer `OnPush` change detection where the project uses it; keep services free of DOM access.
- **Astro:** keep pages static by default and add client-side islands (`client:*`) only where interaction needs them, with the least eager directive that works. Keep data loading in the frontmatter or endpoints, not in client scripts.

## Security and privacy

- Never insert untrusted HTML without the framework's sanitization; avoid `innerHTML` and bypass helpers unless the content is trusted and the reason is written down.
- Keep secrets out of client bundles and environment files shipped to the browser; anything in the client is public.
- Respect the project's Content Security Policy: no inline scripts or styles it forbids, and no new third-party origins without review.
- Analytics, cookies and third-party scripts change personal data flows; flag them, and new dependencies, to the main agent for the specialized reviews.

## Tests

- Unit tests for business logic, formatting, validation and state.
- Component tests for non-trivial observable behavior: rendered states, events, form validation and accessibility roles. Query by role, label or text, not by CSS classes or internal structure.
- End-to-end tests (for example Playwright or Cypress, when the project uses them) for critical user flows across pages, in a local or preview environment.
- Fake the network with HTTP mocks or test servers; never call production APIs from tests.

## Verify

Use the project's scripts, typically formatting in check mode, lint, type check, unit or component tests and the production build. When the project has an accessibility or bundle-size check, run it too.
