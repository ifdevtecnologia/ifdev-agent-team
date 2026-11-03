---
name: supabase-dev
description: Stack guidance for implementing, testing or reviewing a change in a Supabase backend — Postgres schema and migrations, row-level security policies, database functions and triggers, Edge Functions, storage, auth and generated types, with local-only verification. Use when a change touches supabase/ migrations, SQL, policies, Edge Functions or client queries against Supabase.
---

# Supabase development

Apply this on top of the project's own instructions, which win when they are more specific: its `CLAUDE.md`, `AGENTS.md`, `.claude/rules/` and the conventions already in `supabase/`. General rules stay in `~/.claude/rules/engineering-principles.md` and `~/.claude/rules/testing.md`; this skill adds only what is specific to Supabase.

## Local only

- Work against the local stack (`supabase start`) only. Any hosted project or branch, including a preview or development branch, is remote: applying migrations, pushing the schema, running SQL, deploying functions or changing settings there, through the CLI or through connected tools (for example a Supabase MCP server), needs the user's explicit authorization for that operation and project.
- Resetting the local database (`supabase db reset`) is acceptable only when the project's documentation says local data is disposable; otherwise checks that need a reset are BLOCKED, and you ask the main agent.
- Never read or print service role keys, JWT secrets or `.env` contents. The service role key bypasses row-level security and never goes into client code.

## Schema and migrations

- One migration per logical change, created with the project's command and never editing a migration that has already been applied elsewhere; fix forward with a new one.
- Prefer additive, backward-compatible steps (add, backfill, switch readers, then remove) so the running clients keep working. Flag anything that can lose data, lock large tables or break existing clients.
- Constraints belong in the database: `not null`, foreign keys, checks and unique indexes for the rules the data must always respect.
- Add indexes for the columns that policies and frequent queries filter or join on.

## Row-level security

- Enable row-level security on every table exposed through the API, and write policies per operation (`select`, `insert`, `update`, `delete`) with `using` and `with check` as needed.
- Base policies on `auth.uid()` and on membership tables, never on values the client can choose, such as request parameters or `user_metadata` (`raw_user_meta_data`) read through `auth.jwt()`. Wrap `auth.uid()` in a sub-select (`(select auth.uid())`) so it is evaluated once per query.
- Functions declared `security definer` run with elevated privileges: set `search_path = ''` and use schema-qualified names, check the caller inside, and keep them out of exposed schemas when they are internal. New functions are executable by `public` (and, through Supabase's default privileges, by `anon` and `authenticated`): revoke `execute` from those roles, then grant it only to the roles that need it.
- Review grants to the `anon` and `authenticated` roles whenever you create tables, views or functions; views bypass row-level security unless they are created with `security_invoker` (Postgres 15 or later; confirm the version in the project's configuration).

## Edge Functions, storage and auth

- Edge Functions validate input, verify the caller's JWT unless the function is meant to be public, return clear error codes and read secrets only from the function's environment.
- Create private buckets unless public access is intended; access goes through storage policies or signed URLs with short expiry.
- Auth settings, redirect URLs and email templates live in configuration; changing them on a remote project is a remote step.

## Clients

- Regenerate database types with the project's command after a schema change and use them in the clients.
- Select only the columns a screen needs, paginate lists and handle the error of every call; an empty result can also mean a policy denied access.

## Tests

- Database tests (for example pgTAP through `supabase test db`, or the project's own harness) for every policy and function a change touches, covering allowed access and denied access for another user, an anonymous caller and a role without the permission.
- Migration tests: the migrations apply cleanly from scratch on the local stack.
- Edge Function tests with mocked external services, never real providers.
- Flag changes to schema, migrations, functions or policies to the main agent for the specialized reviews.

## Verify

Use the project's commands on the local stack, typically the database lint, the database tests, the migrations applied from scratch and the type generation compared with the committed types. Without Docker or the Supabase CLI these checks are BLOCKED, not passing.
