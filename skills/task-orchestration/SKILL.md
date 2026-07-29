---
name: task-orchestration
description: orchestrate non-trivial tasks with specialized claude code subagents. use when work needs multiple steps, delegation to subagents, implementation plus verification, independent review, root-cause debugging, external research, or trino/sql optimization.
---

# Task orchestration

Act as the task owner. Keep requirements, decisions, implementation, and the final completion decision in the main context. Delegate only focused work that benefits from isolation or specialization.

## Decide whether to orchestrate

Handle trivial, obvious, low-risk changes directly.

Use this workflow when the task has multiple steps, uncertain behavior, meaningful risk, noisy validation, external research, or requires independent review.

## Delegate narrowly

Give every subagent only:

- the exact objective;
- relevant files, commands, versions, and constraints;
- the names of applicable skills to load (never their pasted content);
- required evidence;
- the expected compact output;
- the condition that ends the subtask.

Do not pass the full conversation, unrelated files, or raw historical logs. Ask for conclusions, evidence, and actionable findings.

Use subagents by role:

- `web-researcher-fast`: narrow factual or documentation lookup;
- `docs-researcher-deep`: ambiguous, version-sensitive, or high-impact research;
- `test-runner`: focused tests, linters, type checks, and build checks;
- `debugger`: root-cause analysis after a non-obvious or repeated failure;
- `code-writer`: focused implementation of a single well-scoped feature slice, fix, or refactor;
- `code-reviewer`: adversarial review of non-trivial code changes;
- `sql-data-reviewer`: Trino and analytical SQL correctness and resource review.

Run subagents in parallel only when their work is independent and read-only. Never let parallel workers edit the same files.

## Task brief

Before delegating non-trivial work, compose one structured brief and reuse it for every subagent that needs full context — `code-writer` to implement it, `code-reviewer` and `sql-data-reviewer` to judge the diff against it instead of guessing intent from the code alone. Act like a lead scoping work for an implementer, not writing the code yourself. Include:

- `goal`: the one-sentence outcome.
- `context`: why, plus constraints that shape the approach.
- `files_to_inspect`: the specific files to read before changing anything.
- `changes`: per file, a `file` and a one-line `action` — what must change, not how.
- `risks`: concrete ways the change could break something else.
- `acceptance_criteria`: how to verify done (build, specific test, observed behavior).

Do not implement the change yourself once a brief like this is possible — that is `code-writer`'s job.

Before handing it off:
- If something needed for the brief is missing (unclear goal, unknown files, no acceptance criteria), ask the user directly instead of guessing.
- If you filled gaps by inference, do not proceed silently. Summarize the assumptions against the structure above and get the user to confirm before delegating.

When the work comes from a subagent report — a reviewer finding or a `debugger` diagnosis — do not author a fresh brief and do not summarize. Forward the report's own section verbatim as the brief, adding only the objective and the files in scope. Carry the reporter's qualifiers with it: severity, confidence, and a diagnosis status of `PROBABLE` or `INCONCLUSIVE`. Never present a hedged diagnosis as established, and do not delegate a fix from an `INCONCLUSIVE` or `BLOCKED` report.

## Work loop

1. Define acceptance criteria and identify material risks.
2. Inspect the relevant repository context before researching externally.
3. Create a short plan for non-trivial work. When the plan carries real ambiguity or high risk, stress-test it with the user via the `grilling` skill before building; skip grilling for small, clear, or low-risk changes.
4. Implement the smallest coherent change — directly in the main agent for trivial work, or delegate to `code-writer` with the brief above for anything non-trivial.
5. Run validation through `test-runner`, forwarding the `Verification` section of the implementer's report as-is — the commands already run with their outcomes, and everything it listed as not verified. `test-runner` closes the unverified gaps and the canonical check; it must not repeat a check that already passed. Run a check directly in the main context only when its output is small enough that a subagent round trip costs more than it saves.
6. On failure, identify the root cause before fixing. Fix directly only when the cause is genuinely one-line and obvious; otherwise use `debugger` with the failing command and concise evidence. Never patch just the symptom (suppressing an error, adding a defensive check, retrying) without confirming why the failure actually occurs.
7. After validation passes, hand the same brief to the reviewer alongside the diff, then use:
   - `sql-data-reviewer` for meaningful Trino or analytical SQL changes;
   - `code-reviewer` for non-trivial or high-risk code changes.
8. Triage each review finding without re-deriving it: check it isn't a duplicate or an already-rejected false positive, reject only with evidence, then forward the reviewer's evidence and fix direction verbatim as the brief for `code-writer` — do not paraphrase or re-diagnose. Rerun affected validation and review afterward.
9. Decide whether the task is complete. A subagent verdict is evidence, not the final decision.

## Completion gate

Finish only when all applicable conditions hold:

- explicit requirements and acceptance criteria are satisfied;
- relevant checks actually passed;
- no unresolved critical, high, or medium review findings remain;
- the fix addresses the diagnosed root cause, not just the visible symptom;
- Trino or analytical SQL was reviewed for correctness and cluster cost;
- no validation was disabled or weakened merely to pass;
- no temporary debugging artifacts remain;
- unverified assumptions and limitations are disclosed.

Do not claim a check passed unless it was executed.

## Loop limits

- If a check fails with the same error 2 times in a row: stop editing, revert the last code change, widen the scope of analysis (question earlier assumptions, pull in more context, escalate to `debugger` if not already), and formulate an alternative plan before resuming.
- Allow at most 3 repair cycles for the same failure.
- Allow at most 3 implementation-review cycles.
- Stop earlier if two consecutive cycles produce no measurable progress.
- Do not rerun a subagent with materially identical input unless new evidence exists.
- When limits are reached, stop speculative edits and report `PARTIAL` or `BLOCKED` with the remaining issue and next concrete action.

## Context hygiene

Keep raw logs inside `test-runner` or `debugger`, broad searches inside research agents, and detailed objections inside reviewers. Bring back only decisions, evidence, and unresolved actions.

In the final response state:

1. result: completed, partial, or blocked;
2. changed files and behavior;
3. exact validation performed;
4. relevant review verdicts;
5. remaining limitations or risks.
