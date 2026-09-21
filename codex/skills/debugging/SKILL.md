---
name: debugging
description: Evidence-based root-cause investigation of test failures, runtime errors, crashes, regressions, and unexpected behavior. Use when a check fails or behavior diverges from expectations and the cause is not obvious. Produces a diagnosis backed by evidence before any fix is written.
---

# Debugging

Diagnose before editing. Reproduce or inspect the reported failure, test competing hypotheses, identify the smallest supported root cause, and state a compact diagnosis — only then move to a fix. Keep verbose logs and dead-end exploration out of the diagnosis.

Reason as a pragmatic senior developer: the fix you point at is the minimal one that removes the root cause, built from what already exists rather than from new code or new dependencies.

## Investigation discipline

Until the diagnosis is stated:

- Do not edit project files, apply patches, add debug logging to committed code, or update snapshots.
- Do not install, upgrade, or remove dependencies.
- Do not commit, push, publish, deploy, or modify remote services.
- Do not run destructive commands, migrations, write queries, or commands that mutate external data.
- Do not investigate unrelated warnings or pre-existing failures unless they directly affect the reported issue.

Commands may create normal temporary caches or test artifacts. Disable them when practical, but do not perform broad cleanup or delete user files.

## Investigation workflow

1. Extract the exact symptom: error message or assertion; failing test, command, endpoint, job, or user-visible behavior; expected versus actual; relevant environment or version details.
2. Inspect the smallest relevant context: failing file and stack-trace locations; direct callers and dependencies; nearby tests and configuration; recent changes via `git diff`, `git log`, or `git blame` when useful.
3. Reproduce the issue with the smallest safe command available.
4. Form a short list of plausible hypotheses, ordered by likelihood.
5. Test hypotheses one at a time using read-only inspection or focused commands.
6. Stop when one root cause is strongly supported, or when remaining uncertainty cannot be resolved safely.
7. Verify the diagnosis explains all important observed symptoms, not only one line of the stack trace.

## Debugging rules

- Prefer evidence over intuition.
- Load the relevant skill (`python`, `sql`, `airflow`, `docker`, `godot`, `infra`) when the failing path depends on framework or engine semantics, instead of relying on generic knowledge.
- Distinguish the first meaningful failure from downstream errors.
- Check boundaries where data, control flow, types, state, time zones, concurrency, configuration, or external services enter the failing path.
- Compare expected and actual values as close as possible to the first divergence.
- Consider recent code changes, but do not assume the newest change is responsible without evidence.
- Treat flaky behavior separately from deterministic failures.
- Distinguish code defects from environment, dependency, credential, network, configuration, and test-fixture problems.
- Do not claim certainty when evidence is incomplete.
- Do not suggest broad rewrites when a smaller correction would address the root cause.
- Do not rerun the same command more than once unless the second run tests a specific hypothesis or checks flakiness.
- Never expose secrets, tokens, credentials, or personal data found in logs or configuration.

## Diagnosis contract

State the diagnosis in the user's language, formatted as Markdown, before fixing anything. Use exactly this structure:

### Result
`CONFIRMED`, `PROBABLE`, `INCONCLUSIVE`, or `BLOCKED` — followed by one concise sentence.

### Root cause
The smallest root cause that explains the failure. If the result is not `CONFIRMED`, state clearly what remains uncertain.

### Evidence
2-6 strongest items. Each includes, when available: exact `file:line`; command or observation; relevant value, condition, or error message; how it supports or rejects a hypothesis.

### Reproduction
The minimal command or steps that reliably reproduce the issue. If reproduction was impossible, state why.

### Fix direction
The minimal change to make, targeting the root cause identified above — not a change that would only mask the symptom. Implement it only after the diagnosis is stated, and only within the task's scope.

### Verification
The smallest test or command that should prove the fix works.

### Remaining risks
Only when meaningful: at most 3 related edge cases or uncertainties.

## Noise limits

- Maximum diagnosis length: 700 words.
- No full logs; at most 12 consecutive lines from a stack trace.
- Do not list discarded hypotheses unless they materially narrow the diagnosis.
- Do not repeat the task description back.
- Do not explain your role or debugging methodology.
- Do not add generic advice.
