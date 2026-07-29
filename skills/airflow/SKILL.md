---
name: airflow
description: Apache Airflow DAG and orchestration standards. use for airflow dags, operators, sensors, task scheduling, backfills, and data-pipeline orchestration — typically .py files under dags/.
---

# Airflow

Apply these standards when writing or reviewing Airflow DAGs and orchestration code. Follow the `python` skill for code style and typing, and the `sql` skill for the queries these tasks run (primary target: Trino on Iceberg). When project conventions conflict with the defaults here, the project wins.

## Scheduling

- Prefer `catchup=True` so missed intervals are backfilled — the usual choice for idempotent, interval-partitioned loads where every interval must be processed. Set `catchup=False` only when the pipeline must not run for past intervals, and state why. Set the flag explicitly rather than relying on the Airflow default.
- Default to `max_active_runs=2` on the DAG — cap concurrent runs so a `catchup` backfill doesn't flood the cluster; adjust deliberately per workload.
- Default to `wait_for_downstream=True` in `default_args` — a task waits for its previous run's downstream tasks to finish before starting, keeping interval runs ordered when they write the same table. This forces `depends_on_past=True`, so a missing or stuck prior interval holds the DAG.

## DAG identity

- Hardcode `dag_id` as a string literal — never build it from variables, f-strings, loops, or any runtime value. The id must be statically resolvable at parse time and stable across runs; a computed id silently renames or drops the DAG and orphans its run history.
- Name it `schema_name.table_name` after the DAG's target table.
- Read schema and table names used inside tasks (SQL, operator params, paths) from Airflow Variables instead of hardcoding them in task logic — this is separate from `dag_id`, which stays a literal even though it repeats those names.
- Do not call `Variable.get()` at the top level of the DAG file — it queries the metadata DB on every parse. Use Jinja templating (`{{ var.value.<name> }}`) in templated operator fields, or fetch inside a task.
- Give every DAG a text description of what it does via `description` (or `doc_md` for a longer note) — free-form prose, no fixed structure required.

## Ownership and alerting

- Every DAG must name its author/owning team via an explicit `owner` in `default_args` — Airflow has no separate "author" field, so `owner` carries it (mirror it in the description or a tag if the project does).
- Every DAG must wire up failure alerting — an `on_failure_callback` (plus `sla_miss_callback` where an SLA matters) routing to the project's alert channel, or `email_on_failure=True` with `email` set. Never ship a DAG that fails silently.

## DAG structure

Default shape of a data-loading DAG; deviate only with a stated reason:

- **Entry gate:** before any processing, gate the run on source readiness — a sensor waiting for the upstream partition, file, or table, or an input data-quality (DQ) check.
- **Body:** the transformation and load tasks.
- **Exit:** a DQ check on the output, then table maintenance — when the target is Iceberg (the default), run table optimization (compact small files and expire/clean up old snapshots per project policy; see the `sql` skill for the exact statements).
