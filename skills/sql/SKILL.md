---
name: sql
description: sql coding standards, primary dialect trino/iceberg. use for any work on sql — .sql files, analytical queries, etl/elt transformations, airflow sql tasks, query performance, and sql embedded in other code.
---

# SQL

Apply these standards whenever working with SQL.

## Workflow

1. Inspect the project's SQL dialect, existing query patterns, schema/table layout, and local conventions.
2. Follow project configuration and conventions when they conflict with defaults here — the project always wins.
3. Verify correctness first, then minimize cost.
4. State which checks were run and which were not; never claim a query works unless it was actually executed.

## Style

- Keep SQL keywords UPPERCASE (`SELECT`, `FROM`, `WHERE`, `JOIN`, `GROUP BY`); identifiers lowercase.
- Name columns in `snake_case`, obvious and self-explanatory but not needlessly long (`order_id`, `created_at` — avoid cryptic `oid` and verbose `timestamp_when_order_created`).
- One major clause per line; indent and align subqueries and CTEs for readability.
- Prefer explicit column lists over `SELECT *`.
- Use CTEs (`WITH`) to name steps instead of deep nesting.
- Qualify columns with a table/alias when more than one table is in scope.
- Prefer explicit `JOIN ... ON` with a stated join type over comma joins.

## Correctness

- Do not invent tables, columns, or business rules — verify they exist.
- Be explicit about NULL handling and about duplicate / one-to-many join fan-out.
- Make date/time boundaries and time zones explicit.

## Table design

Applies when defining or reviewing a table (DDL, `CREATE TABLE AS`, or an ETL target) — not to read-only `SELECT`s.

- Give every table a `created_at` timestamp recording when the row was inserted (its load/insert time, distinct from any business event date).
- Add `updated_at` only when existing rows can actually be updated; leave it out for append-only tables.
- Type both columns as `timestamp` at second precision (`timestamp(0)` in Trino) — no sub-second component.
- Keep these columns consistent with the schema's time-zone convention (see date/time handling above).

## Documentation

- When a documentation MCP is connected (e.g. OpenMetadata), consult it before writing or approving SQL: confirm the table's business meaning, grain, and column semantics, and reconcile the query against documented logic — flag any mismatch.
- If a relevant table has missing or empty documentation, propose filling it: draft a short plan (purpose, grain, key columns and their meaning, owners/consumers) and agree it with the user. Only after approval, write it back through the MCP.
- Documenting a table is an outward-facing change — never write docs without explicit approval.

## Performance

- Primary dialect is Trino on Iceberg (see the `sql-data-reviewer` agent); otherwise follow the project's actual dialect.
- Filter on partition columns and push predicates down before joins and aggregations.
- Select only the columns you need; avoid full-table scans.
- Watch for shuffle, spill, and skew on large joins/aggregations; reduce data before wide operations.
- Run non-trivial or production queries through the `sql-data-reviewer` agent.
