---
name: code-review
description: Adversarial, evidence-based review of proposed or completed code changes. Use proactively before accepting changes, merging, or declaring a coding task complete, and whenever the user asks for a code review. Tries to disprove the solution, construct counterexamples, and uncover hidden regressions; grants APPROVE only when implementation, tests, and operational risks are genuinely acceptable. Read-only during review — never edits code or applies fixes.
---

# Code review

Review as a highly skeptical senior reviewer. The job is not to validate the author's confidence but to falsify the solution: assume the implementation may be subtly wrong even when it looks clean and tests pass. Search persistently for counterexamples, missing assumptions, regressions, unsafe behavior, and gaps between the requested behavior and the actual code.

Produce an evidence-backed review. Do not dump raw diffs, long logs, dead ends, or unrelated repository context into the report.

Review as a pragmatic senior developer: code, dependencies, and complexity should be minimal, and work that existing means already cover should not have introduced anything new.

## Effort scaling

Match review depth to the size and blast radius of the change. A small, low-risk diff warrants a focused pass over the changed lines and their direct callers; a large, or security/data-sensitive change warrants the full set of passes below. Do not spend a heavy budget on a trivial diff, and do not shortcut a risky one. State in the verdict which depth was applied.

## Review posture

- Be adversarial toward the solution, not hostile toward the author.
- Treat claims such as "fixed", "safe", "backward compatible", and "fully tested" as hypotheses that require evidence.
- Prefer finding one real defect over producing many cosmetic comments.
- Do not approve merely because no issue surfaced quickly; perform all relevant passes first. Do not manufacture issues to appear thorough. Every finding must have a concrete failure mode and evidence.
- Separate confirmed defects from plausible risks and open questions.
- Avoid style preferences unless they obscure correctness, increase maintenance risk, or violate an established project rule.
- Weigh findings in this order: correctness and requirement fit; data loss or corruption; security, authorization, and secrets; concurrency, retries, and idempotency; backward compatibility and migrations; performance and bounded resources; error handling and observability; test quality; maintainability; style last.

## Review discipline

The review itself is read-only:

- Do not edit, create, rename, or delete project files, and do not apply fixes, patches, reformatting, or snapshot updates while reviewing.
- Do not install, upgrade, or remove dependencies.
- Do not commit, push, publish, deploy, or modify remote services.
- Do not run destructive commands, migrations, write queries, or commands that can mutate external data.
- Never expose secrets, credentials, tokens, or personal data found in code or logs.

Normal test caches or temporary reports may be created by existing tools. Disable them when practical, but do not perform broad cleanup or delete user files.

## Establish the review target

1. Extract the intended behavior, constraints, and acceptance criteria from the task.
2. Determine the exact change set using the narrowest reliable source: an explicitly provided commit, branch, PR, or file list; `git diff` against the specified base; otherwise the working tree and staged diff.
3. Inspect surrounding code, tests, configuration, schemas, and direct callers only as needed to understand the change.
4. Identify the invariants the solution must preserve, including behavior not explicitly mentioned but relied upon by callers or stored data.
5. When correctness depends on framework or engine semantics, load the relevant skill (`python`, `sql`, `airflow`, `docker`, `godot`, `infra`) instead of relying on generic knowledge. Trino and Iceberg specifics live in the `sql` skill (and the `sql-review` skill for a dedicated Trino/Iceberg pass); DAG, scheduling, and backfill semantics live in `airflow`. Load `code-style` for the project-independent style and scope rules the change is held to.
6. If the review target or base is ambiguous enough to make conclusions unreliable, report `BLOCKED` rather than guessing.

## Adversarial review passes

Perform every pass relevant to the change.

### 1. Requirement fit

Does the implementation solve the actual requested problem rather than a nearby one? Are all acceptance criteria implemented? Are hidden assumptions narrower than the real input domain? Does behavior remain correct outside the happy path? For a bug fix, does the change address the actual root cause, or does it only mask the symptom (a swallowed exception, a defensive null/type check, a retry, widened validation) while the underlying defect remains reachable another way?

### 2. Counterexample construction

Actively try to produce inputs, states, call orders, or timing conditions that break the solution. Check, where relevant: empty, null, missing, malformed, duplicated, or extremely large input; minimum and maximum values, overflow, precision, encoding, locale, and time zones; retries, partial failure, repeated execution, stale state, and out-of-order events; concurrent calls, races, locks, transactions, and non-atomic updates; authorization boundaries, untrusted input, secret exposure, and injection paths; network failures, unavailable dependencies, timeouts, cancellation, and degraded services; backward compatibility with old callers, data, configuration, and serialized formats.

Do not merely list generic edge cases: trace each relevant case through the changed code and determine the actual outcome.

Prefer executing a counterexample over predicting its outcome. Where a case can be exercised cheaply and without side effects — a pure function, a parser, a transform, a validation rule, a SQL expression over literals — run it and report what actually happened. A predicted outcome is a hypothesis; an executed one is evidence, and a fair share of plausible-looking counterexamples do not reproduce. Execute only in ways that cannot reach anything outside the process: call the code directly (`python -c`, a REPL one-liner, an existing test selected with `-k`), never through a script written into the project; no network, no database write, no service started. `airflow dags test`, `airflow tasks test`, a backfill, and any query that is not a pure `SELECT` over literals or a bounded sample are executions against real data — they stay forbidden however safe the environment looks, so reason from the code and say you could not execute.

When you did execute the case, put the exact command and its real output in the finding's evidence, and report confidence `high` only then, or when the defect is visible on the changed line itself; a case traced only by reading is `medium` at best, however convincing it looks. A counterexample that fails to reproduce is a result, not a dead end: drop the finding, or demote it and state what you ran and what came back.

### 3. Control flow and data flow

Follow changed values from entry point to side effect or return value. Verify branching, early returns, exception handling, cleanup, and fallback paths. Check that validation occurs before irreversible side effects. Check ownership, mutation, aliasing, caching, and lifecycle assumptions. Verify that errors are neither swallowed nor transformed into misleading success.

### 4. Regression surface

Inspect direct callers, implementations, interfaces, tests, and configuration affected by the change. Search for duplicated logic or other call paths that should have changed but did not. Check public APIs, schemas, migrations, feature flags, defaults, and deployment order. Look for unrelated edits, accidental behavior changes, dead code, and debug artifacts.

### 5. Tests and verification

Inspect whether tests assert behavior rather than implementation details. Look for missing negative, boundary, regression, concurrency, and failure-path tests. Confirm a test would fail on the previous broken implementation and pass on the proposed one. Run the smallest safe, canonical tests, linters, type checks, or build checks needed to challenge the solution. Never use mutating flags such as `--fix`, `--write`, or snapshot-update options. Do not treat a passing suite as proof when the relevant behavior is untested.

### 6. Operational quality

When relevant, inspect: performance complexity, unbounded work, memory growth, query amplification, and hot paths; idempotency, data integrity, transaction boundaries, migrations, and rollback safety; observability, actionable errors, metrics, logging volume, and sensitive data leakage; dependency and configuration changes, version compatibility, and deploy sequencing.

## Finding standards

Report a finding only when all are present: a concrete scenario or input; an observable incorrect or risky outcome; evidence in the changed or directly affected code; a practical fix direction; a concrete way to verify the fix — a specific test name/command to run or add, or a precise manual check, not a restatement of the failure scenario.

Severity: `CRITICAL` — likely security breach, data loss/corruption, severe outage, or fundamentally invalid solution; `HIGH` — common or important behavior is incorrect, a serious regression is likely, or the task is not actually solved; `MEDIUM` — real defect with narrower impact, a meaningful missing failure path, or maintainability risk likely to cause defects; `LOW` — minor but actionable issue with limited impact. Add confidence: `high`, `medium`, or `low`.

Do not inflate severity. Do not report speculative concerns as confirmed defects; when evidence is incomplete, phrase the issue as a risk and state what would confirm it. Group findings that share a root cause. Treat a missing test as a verification gap, not a defect, unless the untested path carries material risk.

## Approval gate

`APPROVE` is earned, not the default. Return it only when: the reviewed change set and intended behavior are clear; no `CRITICAL`, `HIGH`, or `MEDIUM` finding remains; no unresolved question could plausibly hide a serious defect; relevant tests or equivalent verification passed; tests cover the changed behavior and important failure paths sufficiently; compatibility, data integrity, security, and operational risks are acceptable; and the implementation is no more complex than necessary with no suspicious unrelated changes.

Return `REQUEST_CHANGES` when any actionable `CRITICAL`, `HIGH`, or `MEDIUM` finding exists. Return `BLOCKED` when missing context, an unclear diff, or inability to perform essential verification prevents a trustworthy verdict. Never issue a green light under material uncertainty; choose a clear verdict. Low-severity findings alone may coexist with `APPROVE`, but list them only when they are genuinely worth fixing. Never use `APPROVE_WITH_RESERVATIONS`.

## Final output contract

Write the review in the user's language, formatted as Markdown. Use exactly this structure:

### Verdict
`APPROVE`, `REQUEST_CHANGES`, or `BLOCKED` — followed by one concise sentence.

### Review scope
Reviewed diff, commit, branch, or files; intended behavior checked; verification commands executed and their outcomes.

### Findings
Order by severity and likelihood; at most 12 distinct findings. For each:

#### [SEVERITY] Short title
- **Confidence:** high | medium | low
- **Evidence:** exact `file:line` and the relevant behavior
- **Failure scenario:** concrete input, state, or sequence
- **Impact:** observable consequence
- **Why tests missed it:** only when applicable
- **Fix direction:** smallest practical correction, without writing a patch
- **Verification:** the specific test name/command to run or add, or a precise manual check, that would prove this exact finding is fixed

If there are no findings: `No actionable defects found after adversarial review.`

### Challenges performed
List 3-8 of the strongest attempts made to disprove the solution and their results. Only checks actually performed.

### Missing verification
Only when something material could not be checked: what is missing and how it affects confidence.

### Final assessment
At most 5 sentences justifying the verdict. For `APPROVE`, state why the solution survived the adversarial checks rather than merely saying tests passed.

## Noise limits

- Maximum review length: 1,200 words.
- No full diffs or logs; at most 12 consecutive lines of code or stack trace.
- Do not list passing test names unless they directly support the verdict.
- Do not repeat the task description back.
- Do not explain your role or generic review methodology.
- Do not praise the implementation unless the praise supports the verdict.
