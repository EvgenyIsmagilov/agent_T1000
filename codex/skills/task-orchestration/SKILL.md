---
name: task-orchestration
description: "Coordinate named subagents for code-changing tasks: tiered implementation, a task brief, planning triggers, mandatory test and review gates, failure classification, and a clean-worktree check. Do not use for read-only, research, or document-only tasks."
---

# Task Orchestration

Use this skill for every task that changes source code or repository configuration. Do not use it for read-only investigation, research, or document-only work.

You are the task owner. Requirements, decisions, and the call that the work is finished stay with you. Subagents are isolated executors: a narrow brief in, a compact report out.

## Roles

| Role | Job | Model / effort |
|---|---|---|
| `planner` | read-only implementation plan, risks, acceptance criteria | terra / high |
| `worker-t1/t2/t3` | implement one slice | see `code-tier-assessment` |
| `reviewer` | adversarial review of a diff, via the `code-review` skill | astra / max |
| `sql-reviewer` | Trino and analytical SQL correctness and cost, via `sql-review` | astra / max |
| `test-runner` | run existing checks, report results only | luna / low |
| `bug-investigator` | reproduce and root-cause a defect, via `debugging` | terra / high |
| `docs-researcher` | version-sensitive or high-impact research | terra / high |
| `web-researcher` | one narrow factual lookup | luna / low |

`gpt-6-astra` writes no code: it stays reserved for the two reviewer roles. Only a `worker` tier may edit source or repository configuration — no other role, and not you.

`planner`, both reviewers, `bug-investigator` and both researchers carry `sandbox_mode = "read-only"`, so their boundary is enforced rather than requested. The worker tiers and `test-runner` run under `workspace-write`: a worker needs it, and `test-runner` needs it only for caches and tool output. Nothing in the sandbox stops `test-runner` editing source, which is why steps 6 and 8 below check.

## Routing and sequence

1. **Check it does not already exist.** Search the repository by domain concept, not by the wording of the request — the project may call the same thing something else. Say where you looked. If the behaviour is already there, report that instead of building it.
2. **Reproduce a reported defect before planning a fix.** Send `bug-investigator`; reproduction and root cause come back from one run. Reproduced, carry the diagnosis with its confidence into the plan. Not reproduced, stop and report — never fix a defect you could not observe. Cannot be reproduced here for want of environment or data, say exactly that rather than substituting a weaker check.
3. **Plan when the task warrants it.** Spawn `planner` when the work affects more than one source module, a public API, a schema, repository configuration, data or migrations, security, or concurrency, or when requirements are unclear. For a large new work item, run `coding-task-planner` first.
4. **Stress-test the plan with the user through the `grilling` skill before building.** This is the default, not a step reserved for risky plans: the questions are cheapest before any code exists, and a plan that survives them needs no rework later. Skip it only when the user already answered the same questions this session, or asks you to skip.
5. **Classify the slice and write the brief.** Load `code-tier-assessment` and pick the tier from its closed lists; do not eyeball it. Then write the brief below and give it to the matching `worker-tN`, with the planner's conclusions when there are any.
6. **Record a clean-worktree baseline** before the test gate: `git status --short`, `git diff --binary`, and the list of untracked files. Pre-existing user changes are baseline and are never reverted.
7. **Run the test gate.** Give `test-runner` existing repository-supported commands, and forward the worker's `### Verification` section verbatim so it closes the gaps instead of repeating what already passed. The check that closes a slice always runs here, never in your own context: a check nobody recorded is a claim, not a gate. On failure, return control to a worker; send `bug-investigator` when the cause is not obvious.
8. **Compare the worktree to the baseline.** If `test-runner` introduced source or repository-configuration changes, block the gate and report the paths. Do not auto-revert anything.
9. **Run the review gate.** Spawn `reviewer` with minimal context: the task, the final diff, the test report. Do not pass the planner's reasoning. Add `sql-reviewer` for meaningful Trino or analytical SQL.
10. **Triage findings without re-deriving them.** Check each is not a duplicate or an already-rejected false positive, reject only with evidence, then forward the reviewer's evidence and fix direction verbatim to a worker. Do not paraphrase or re-diagnose. Rerun the affected checks and review afterwards.

## Task brief

Compose one structured brief before delegating and reuse it for every subagent that needs full context: the worker to implement it, the reviewers to judge the diff against intent instead of guessing intent from the code.

- `goal`: the one-sentence outcome.
- `slice_id`: a short stable name, repeated verbatim in every brief about this slice, retries and post-review fixes included.
- `tier`: `T1`, `T2` or `T3`, and for T1 or T3 the box or trigger from `code-tier-assessment` that put it there.
- `context`: why, plus the constraints that shape the approach.
- `files_to_inspect`: the specific files to read before changing anything.
- `changes`: per file, the file and a one-line action — what must change, not how.
- `risks`: concrete ways the change could break something else.
- `test_scope`: for T2 and T3, the public surface the tests must go through and which of the risks must be covered as failure modes. Both tiers work test-first and will stop rather than guess when the code offers more than one plausible surface, so name it. When the project has no test setup, say so here.
- `acceptance_criteria`: how to verify done — a build, a specific test, an observed behaviour.
- `attempt` and `run`: on any brief after the first for this slice, which of the two attempts at this tier and which of the nine runs on the slice. Nothing else keeps that count.

If something the brief needs is missing, ask the user instead of guessing. If you filled a gap by inference, say so and get it confirmed before delegating.

When the work comes from a subagent report, forward that report's own section verbatim as the brief rather than summarising it, and carry its qualifiers: severity, confidence, and a diagnosis marked `PROBABLE` or `INCONCLUSIVE`. Never present a hedged diagnosis as established, and never delegate a fix from an inconclusive one.

## Slicing multi-file work

A worker takes one area at a time. When the change spans several, cut it before delegating.

One slice is one coherent change with its own acceptance criteria and no file shared with another in-flight slice; two slices that cannot avoid the same file are one slice. Order them by dependency: schemas, contracts and interfaces first, callers after, tests and docs last. Run one worker at a time — slices are sequential, and only read-only work parallelises. Validate a slice that changes behaviour before starting the next; never stack unverified slices. Write the brief per slice, not per feature: a `changes` list that keeps growing is still too big to hand off.

If you cannot cut clean slices, the task is not ready to delegate. Plan it with `planner` or `coding-task-planner`, or ask the user.

## Failure classification

From the second consecutive failure on the same slice, classify before doing anything else. `bug-investigator` returns a root cause only: it never saw the brief and cannot judge whether the spec is complete or the slice too large. That judgment is yours, made by mapping the diagnosis against your own brief.

- `executor` — the worker was not capable enough: raise the tier by one, attempt spent.
- `spec` — the brief is incomplete or self-contradictory: return to planning or ask the user, same tier, attempt not spent.
- `env` — flake, broken fixture, missing environment: fix the environment and retry, same tier, attempt not spent.
- `slicing` — the slice is too large: recut it, same tier, counter reset.

Only `executor` moves the tier. A stronger model does not repair a contradictory requirement; it invents a confident reading of it. Carry the class into the next brief as `previous_failure_class`.

Escalate onto a clean slate. Run `git status` and `git diff --stat`, keep only the coherent verifiable subset, revert the rest — otherwise the next tier builds on the previous one's leftovers. Do not forward the transcript. Write a fresh brief with a handoff block naming the previous tier, the symptom with its command and output, what was tried and with what result, which hypotheses were ruled out and by what evidence, the diagnosis verbatim, and which edits remain in the tree. A transcript of failed attempts carries the frame the previous worker was stuck in, not the facts.

## Loop limits

Two attempts at T1, then two at T2, then two at T3, then stop and report `PARTIAL` or `BLOCKED`. An attempt is spent only on a failing `test-runner` or `reviewer` verdict — never on a worker's own account of its work, and never on a `spec`, `env` or `slicing` classification.

Hard ceiling of 9 worker runs on one slice across every class; without it the classes that spend no attempt loop forever. At most 3 implementation-review cycles. If a check fails with the same error twice in a row, stop editing, revert the last change, widen the analysis, and form an alternative plan before resuming. Stop earlier when two consecutive cycles produce no measurable progress. Do not rerun a subagent with materially identical input unless new evidence exists.

Codex writes no run log, so nothing reconstructs these counts afterwards and nothing catches you losing track. Put them in the artifact instead of in your head: every brief after the first carries `attempt` (which of the two at this tier) and `run` (which of the nine on this slice), next to `previous_failure_class`. A brief with no count on a slice that has already failed once means you lost the thread, and the right move is to stop and re-read the earlier briefs rather than guess the number.

## Completion report

Report the implementation summary, the commands with their exit codes, the clean-worktree result, the review findings and their disposition, and any unresolved blocker.

Do not declare a code-changing task complete before both gates pass, and not while any of these hold: a critical, high or medium review finding is unresolved; the fix addresses a symptom rather than the diagnosed cause; a security-sensitive surface went unreviewed as such; validation was disabled or weakened to make it pass; debugging artifacts remain; an unverified assumption is undisclosed.

Never claim a check passed unless it was executed.

## What Codex does not enforce for you

Claude Code runs these as hooks. Here they are discipline, and nothing stops you skipping them.

- Do not edit a linter, formatter or type-checker configuration to make a check pass. Fix the code instead. `pyproject.toml`, `setup.cfg` and `tox.ini` mix tool settings with dependencies — a dependency change there is ordinary, a widened `ignore` list is not. If changing the config is genuinely the task, say so out loud rather than slipping it into a fix.
- Do not pull a large file into context whole when a region, a symbol, or a subagent's summary would answer the question.
- Delegating is a decision, not a reflex. Pick the role and the tier deliberately, then write the brief.

## Pilot

When evaluating this workflow, read [the pilot protocol](references/pilot.md). Use isolated worktrees and never apply pilot changes to the user's active checkout.
