---
name: code-review
description: "Adversarial, evidence-based review of a code change against its spec, the repo's standards, and every way it could break. Use when the user wants to review a branch, a PR, work-in-progress changes, or asks to \"review since X\", and before accepting, merging, or declaring a coding task complete. Checks spec coverage, tries to disprove the solution with counterexamples, flags standards breaches and code smells, and returns one verdict. The review itself is read-only — it never edits code or applies fixes."
---

# Code review

One review, three questions about the same diff:

- **Spec coverage** — does the change do what the task asked, all of it and nothing else?
- **Defects** — can the solution be broken? Assume it may be subtly wrong even when it looks clean and tests pass, and try to falsify it.
- **Standards** — is it written the way this repo documents, without obvious smells?

They are reported in separate blocks so that one cannot hide another: ten style remarks must not bury a missing requirement, and "does exactly what was asked" must not excuse a broken invariant. Only spec gaps and defects decide the verdict.

## Who runs the review

- **Main agent, asked by the user** (`/code-review`, "review since X", "review this PR"): do not review in your own context. Pin the target (step 1–2 of **Establish the review target**), then delegate to the `reviewer` subagent with the diff command, the commit list, the spec source or its contents, and the instruction to load this skill. Relay its report as-is. Only when no `reviewer` subagent is available in this harness, run the review yourself by the rules below and say so.
- **`reviewer` subagent, or any agent told to review**: follow the rest of this skill.

## Posture

- Be adversarial toward the solution, not hostile toward the author.
- Treat claims such as "fixed", "safe", "backward compatible", and "fully tested" as hypotheses that require evidence.
- Prefer finding one real defect over producing many cosmetic comments. Do not manufacture issues to appear thorough: every finding needs a concrete failure mode and evidence.
- Do not approve merely because no issue surfaced quickly; perform all relevant passes first.
- Separate confirmed defects from plausible risks and open questions.
- Review as a pragmatic senior developer: code, dependencies, and complexity should be minimal, and work that existing means already cover should not have introduced anything new.
- Weigh findings in this order: correctness and requirement fit; data loss or corruption; security, authorization, and secrets; concurrency, retries, and idempotency; backward compatibility and migrations; performance and bounded resources; error handling and observability; test quality; maintainability; style last.

## Effort scaling

Match depth to the size and blast radius of the change. A small, low-risk diff gets a focused pass over the changed lines, their direct callers, and relevant tests. A medium change adds callers, dependencies, edge cases, and regression risks. A change touching security, data integrity, concurrency, migrations, or public APIs gets every pass below. Do not spend a heavy budget on a trivial diff, and do not shortcut a risky one. State in the verdict which depth was applied.

## Review discipline

The review is read-only:

- Do not edit, create, rename, or delete project files; do not apply fixes, patches, reformatting, or snapshot updates.
- Do not install, upgrade, or remove dependencies.
- Do not commit, push, publish, deploy, or modify remote services.
- Do not run destructive commands, migrations, write queries, or commands that can mutate external data.
- Never expose secrets, credentials, tokens, personal data, or sensitive production values found in code or logs.

The reviewer posts nothing to a PR/MR. To publish findings as comments, the main agent loads the `git` skill after the user approves, and follows `references/review-comments.md` there.

Normal test caches or temporary reports may be created by existing tools. Disable them when practical, but do not perform broad cleanup or delete user files.

## Establish the review target

1. **Pin the change set** from the narrowest reliable source: an explicitly provided commit, branch, PR, or file list; `git diff <base>...HEAD` (three-dot, so the comparison is against the merge-base) plus `git log <base>..HEAD --oneline`; otherwise the working tree and staged diff. Confirm the base resolves (`git rev-parse <base>`) and the diff is non-empty before going further. If no base was named and none is implied, ask the user — or, as a subagent that cannot ask, report `BLOCKED` naming what is missing.
2. **Find the spec**, in this order: the task or brief you were given; issue references in commit messages (`#123`, `Closes #45`, GitLab `!67`) when an issue-tracker tool is available to fetch them; a path the user passed; a spec file under `docs/`, `specs/`, or `.scratch/` matching the branch or feature. If nothing is found, record "no spec available" — Spec coverage then says so and the review continues on the other two questions.
3. **Find the standards**: anything in the repo that documents how code is written (`CODING_STANDARDS.md`, `CONTRIBUTING.md`, `AGENTS.md`, `CLAUDE.md`, linter configs), plus the `code-style` skill.
4. Inspect surrounding code, tests, configuration, schemas, and direct callers only as needed. Identify the invariants the change must preserve, including behavior callers or stored data rely on without it being written down.
5. Load the skills the change calls for instead of relying on generic knowledge:
   - `python`, `sql`, `airflow`, `docker`, `godot`, `infra` when correctness depends on that stack;
   - `sql-review` and `sql` for meaningful SQL, ETL/ELT, or analytical SQL — perform their full correctness and cluster-cost workflow as part of this review;
   - `appsec-review` when the diff touches a security surface: authentication, authorization, secrets, untrusted input, data exposure, dependencies, or container and server configuration.
6. If the target or base is ambiguous enough to make conclusions unreliable, report `BLOCKED` rather than guessing.

## Re-review

When the brief carries the findings of a previous review together with a delta diff (the change since the reviewed state — usually `git diff refs/snapshots/<slice_id>/<prev> refs/snapshots/<slice_id>/<new>` between the orchestrator's snapshots), this is a re-review, not a fresh one:

1. Check each previous finding first and mark it `fixed`, `not fixed`, or `regressed`, with evidence. A `not fixed` or `regressed` finding stays in Findings at its original severity unless the evidence changed.
2. Then run the passes over the delta diff only, at the depth its own size and blast radius call for.
3. Widen to the full change set only when the fix reaches beyond the code the previous findings named — a new file, a changed contract, a touched caller.

Name the depth applied in the verdict (`re-review, delta only` or `re-review, widened`). Without previous findings in the brief, review from scratch — a re-review never relies on memory of an earlier run.

## Review passes

Perform every pass relevant to the change.

### 1. Spec coverage

List each requirement or acceptance criterion from the spec and mark it done, partial, or missing. Then look the other way: behavior in the diff nobody asked for (scope creep), and requirements that look implemented but where the implementation looks wrong. Quote the spec line for each item. For a bug fix, check that the change addresses the root cause rather than masking the symptom (a swallowed exception, a defensive null/type check, a retry, widened validation) while the defect stays reachable another way. A missing or wrong requirement is a defect and also goes into Findings.

### 2. Counterexample construction

Actively try to produce inputs, states, call orders, or timing conditions that break the solution. Check, where relevant: empty, null, missing, malformed, duplicated, or extremely large input; minimum and maximum values, overflow, precision, encoding, locale, and time zones; retries, partial failure, repeated execution, stale state, and out-of-order events; concurrent calls, races, locks, transactions, and non-atomic updates; authorization boundaries, untrusted input, secret exposure, and injection paths; network failures, unavailable dependencies, timeouts, cancellation, and degraded services; backward compatibility with old callers, data, configuration, and serialized formats.

Do not merely list generic edge cases: trace each relevant case through the changed code and determine the actual outcome.

Prefer executing a counterexample over predicting its outcome. Where a case can be exercised cheaply and without side effects — a pure function, a parser, a transform, a validation rule, a SQL expression over literals — run it and report what actually happened. A predicted outcome is a hypothesis; an executed one is evidence, and a fair share of plausible-looking counterexamples do not reproduce. Execute only in ways that cannot reach anything outside the process: call the code directly (`python -c`, a REPL one-liner, an existing test selected with `-k`), never through a script written into the project; no network, no database write, no service or container started. `airflow dags test`, `airflow tasks test`, a backfill, mutations, DDL, production writes, `EXPLAIN ANALYZE`, and unbounded `SELECT` against shared, staging, or production data stay forbidden however safe the environment looks. For plan evidence, ordinary `EXPLAIN` is permitted only through `mcp__trino__explain_query`; when that tool is unavailable, record the missing evidence and never substitute workload execution.

When you did execute the case, put the exact command and its real output in the finding's evidence, and report confidence `high` only then, or when the defect is visible on the changed line itself; a case traced only by reading is `medium` at best. A counterexample that fails to reproduce is a result, not a dead end: drop the finding, or demote it and state what you ran and what came back.

### 3. Control flow and data flow

Follow changed values from entry point to side effect or return value. Verify branching, early returns, exception handling, cleanup, and fallback paths. Check that validation occurs before irreversible side effects. Check ownership, mutation, aliasing, caching, and lifecycle assumptions. Verify that errors are neither swallowed nor turned into misleading success.

### 4. Regression surface

Inspect direct callers, implementations, interfaces, tests, and configuration affected by the change. Search for duplicated logic or other call paths that should have changed but did not. Check public APIs, schemas, migrations, feature flags, defaults, and deployment order. Look for unrelated edits, accidental behavior changes, dead code, and debug artifacts.

### 5. Tests and verification

Check that tests assert behavior rather than implementation details. Look for missing negative, boundary, regression, concurrency, and failure-path tests. Confirm a test would fail on the previous broken implementation and pass on the proposed one. Run the smallest safe, canonical tests, linters, type checks, or build checks needed to challenge the solution. Never use mutating flags such as `--fix`, `--write`, or snapshot-update options. Do not treat a passing suite as proof when the relevant behavior is untested.

### 6. Operational quality

When relevant: performance complexity, unbounded work, memory growth, query amplification, and hot paths; idempotency, data integrity, transaction boundaries, migrations, and rollback safety; observability, actionable errors, metrics, logging volume, and sensitive data leakage; dependency and configuration changes, version compatibility, and deploy sequencing.

### 7. Standards and smells

Check the diff against the documented standards found in step 3, citing the file and rule for each breach. On top of them, always apply this smell baseline (Fowler, _Refactoring_, ch. 3). Two rules bind it: a documented repo standard always wins, and where it endorses something the baseline would flag, suppress the smell; each smell is a labelled judgement call ("possible Feature Envy"), never a hard violation. Skip anything tooling already enforces.

- **Mysterious Name** — a name that does not reveal what it does or holds → rename; if no honest name comes, the design is murky.
- **Duplicated Code** — the same logic shape in more than one hunk or file → extract the shared shape.
- **Feature Envy** — a method reaching into another object's data more than its own → move it onto that data.
- **Data Clumps** — the same few fields or params travelling together → bundle them into one type.
- **Primitive Obsession** — a primitive standing in for a domain concept → give the concept its own small type.
- **Repeated Switches** — the same `switch`/`if`-cascade on the same type recurs → polymorphism, or one shared map.
- **Shotgun Surgery** — one logical change forces scattered edits → gather what changes together.
- **Divergent Change** — one module edited for several unrelated reasons → split by reason to change.
- **Speculative Generality** — abstraction or hooks the spec does not need → delete, inline until a real need shows.
- **Message Chains** — long `a.b().c().d()` navigation → hide the walk behind one method.
- **Middle Man** — a class or function that mostly delegates → call the real target directly.
- **Refused Bequest** — a subclass ignoring most of what it inherits → composition instead of inheritance.

A breach that causes or is likely to cause a defect is a finding; everything else stays in Standards & smells and never blocks.

## Finding standards

Report a finding only when all are present: a concrete scenario or input; an observable incorrect or risky outcome; evidence in the changed or directly affected code; a practical fix direction; a concrete way to verify the fix — a specific test name or command to run or add, or a precise manual check, not a restatement of the failure scenario.

Severity: `CRITICAL` — likely security breach, data loss/corruption, severe outage, or fundamentally invalid solution; `HIGH` — common or important behavior is incorrect, a serious regression is likely, or the task is not actually solved (a missing core requirement lands here); `MEDIUM` — real defect with narrower impact, a meaningful missing failure path, or maintainability risk likely to cause defects; `LOW` — minor but actionable issue with limited impact. Add confidence: `high`, `medium`, or `low`.

Do not inflate severity. Do not report speculative concerns as confirmed defects; when evidence is incomplete, phrase the issue as a risk and state what would confirm it. Group findings that share a root cause. Treat a missing test as a verification gap, not a defect, unless the untested path carries material risk.

## Approval gate

`APPROVE` is earned, not the default. Return it only when: the change set and intended behavior are clear; every requirement in Spec coverage is done, or the gap is explicitly out of scope; no `CRITICAL`, `HIGH`, or `MEDIUM` finding remains; no unresolved question could plausibly hide a serious defect; relevant tests or equivalent verification passed and cover the changed behavior and important failure paths; compatibility, data integrity, security, and operational risks are acceptable; and the implementation is no more complex than necessary with no suspicious unrelated changes.

Test evidence from the brief counts toward that gate when it names the command and its outcome as recorded by `test-runner` (or the implementer's `Verification` section that `test-runner` confirmed); do not rerun it. Run tests yourself only as counterexamples, or when the reported evidence is missing, did not run, or does not reach the changed behavior — and say which in `Review scope`. An implementer's unconfirmed claim that tests pass is not evidence.

Return `REQUEST_CHANGES` when any actionable `CRITICAL`, `HIGH`, or `MEDIUM` finding exists. Return `BLOCKED` when missing context, an unclear diff, unavailable dependencies, or inability to perform essential verification prevents a trustworthy verdict. Never issue a green light under material uncertainty. Low findings and smells alone may coexist with `APPROVE`. Never use `APPROVE_WITH_RESERVATIONS`.

## Output contract selection

Choose one contract for the whole review and issue one verdict:

- **Code without meaningful SQL** — the structure below.
- **SQL-only** — the `sql-review` skill's SQL-only structure instead of the one below.
- **Mixed code and SQL** — the structure below. Inside `### Review scope` add `**SQL query profile:**` and `**SQL resource risks:**` as `sql-review` defines them; put SQL correctness and cost findings in the same `### Findings` list; fold SQL challenges and missing plan evidence into the matching sections. Never a second verdict, findings list, or final assessment.
- **Security findings** from `appsec-review` go into the same `### Findings` list, in whichever structure applies.

## Report structure

Write the review in the language of the task, formatted as Markdown, using exactly this structure:

### Verdict
`APPROVE`, `REQUEST_CHANGES`, or `BLOCKED` — one concise sentence, naming the review depth applied.

### Review scope
Reviewed diff, base, commits, or files; spec source (or "no spec available"); standards sources used; verification commands executed and their outcomes. For a mixed review, also the SQL query profile and resource risks.

### Spec coverage
One line per requirement: `done` / `partial` / `missing`, with the quoted spec line; then unrequested behavior, if any. If there is no spec: `No spec available.`

### Findings
Defects only — spec gaps, bugs, regressions, security and data risks. Order by severity and likelihood; at most 12. For each:

#### [SEVERITY] Short title
- **Confidence:** high | medium | low
- **Evidence:** exact `file:line` and the relevant behavior
- **Failure scenario:** concrete input, state, or sequence
- **Impact:** observable consequence
- **Why tests missed it:** only when applicable
- **Fix direction:** smallest practical correction, without writing a patch
- **Verification:** the specific test name or command to run or add, or a precise manual check, that would prove this exact finding is fixed

If there are none: `No actionable defects found after adversarial review.`

### Standards & smells
Documented-standard breaches (cite file and rule) and baseline smells (name the smell, quote the hunk), each marked as a judgement call; they do not affect the verdict. At most 8. If none, omit the section.

### Challenges performed
3–8 of the strongest attempts to disprove the solution and their results. Only checks actually performed.

### Missing verification
Only when something material could not be checked: what is missing and how it affects confidence.

### Final assessment
At most 5 sentences justifying the verdict. For `APPROVE`, state why the solution survived the adversarial checks rather than merely saying tests passed.

## Noise limits

- Maximum review length: 1,300 words.
- No full diffs or logs; at most 12 consecutive lines of code or stack trace.
- Do not list passing test names unless they directly support the verdict.
- Do not repeat the task description back.
- Do not explain your role or generic review methodology.
- Do not praise the implementation unless the praise supports the verdict.
