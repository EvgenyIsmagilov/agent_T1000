---
name: code-tier-assessment
description: "Classify an implementation slice into T1, T2 or T3 before delegating it to a worker, using closed-list criteria on file and line scope plus criticality and difficulty. Use before writing a task brief inside the task-orchestration workflow."
---

# Code tier assessment

Classify every slice before writing its brief, and only after looking at the affected code — the goal statement alone does not tell you what the change actually touches. Pick the tier from checkable facts, never from a feeling of difficulty: vague sizing is how tiers drift toward whichever one feels safest.

## The three tiers

| Tier | Agent | Model | Effort |
|---|---|---|---|
| T1 | `worker-t1` | gpt-5.6-luna | max |
| T2 | `worker-t2` | gpt-5.6-terra | medium |
| T3 | `worker-t3` | gpt-5.6-terra | max |

`gpt-6-astra` writes no code. It stays reserved for `reviewer` and `sql-reviewer`: review benefits from the strongest judgment on someone else's finished work, while writing benefits more from tiering, because most slices are not hard.

This rubric carries no target percentages. Codex keeps no run log, so nothing could check them, and a share nobody can measure lets routing drift while the document still claims a goal. The lists below are the whole control: T2 unless a box or a trigger says otherwise.

## Default: T2

Start every slice at T2 unless it clearly clears the T1 checklist or trips a T3 trigger. T2 is the safe default for ordinary feature work and most bug fixes, including moderate ones you have not fully sized yet.

## Send to T1 only when every box is checked

- the change is a one-line or small, self-contained edit, or a minor correction (rename, constant, config value, an obvious check the spec already calls for);
- the diff stays under 100 lines in any single file, and under 200 lines in total across every file it touches;
- the slice touches at most 5 files;
- none of the T3 triggers below apply.

Missing any box keeps it at T2. Do not round down because the rest of the task feels easy.

## Send to T3 when any one trigger fires

Criticality, regardless of how small the diff looks:

- migrations and schema changes;
- money and billing;
- authentication, authorization, secrets, permissions;
- external contracts and public APIs;
- deletion or irreversible overwrite of data.

Difficulty and scale:

- no clear approach exists; the slice needs a design decision;
- correctness depends on non-obvious interaction between several subsystems;
- correctness cannot be settled by running a check — it needs an argument about invariants;
- the slice touches more than 15 files, however small each individual change is.

## Examples

- rename a config key used in 3 files, 40 lines total → T1.
- add a new field end to end (model, API, UI) → T2, ordinary feature work.
- add a column via a migration, even if the diff is 5 lines → T3, criticality trigger, not size.

## Naming the tier

Name the tier in the brief's `tier` field, and for T1 or T3 name the specific box or trigger that put it there. A slice with no named trigger for T1 or T3 defaults to T2: the lists here are checkable, a hunch is not.

## Escalation

Each tier gets 2 attempts (see `task-orchestration`, Loop limits). A failure classified `executor` moves the slice up exactly one tier, T1 to T2 or T2 to T3, never skipping. T3 has nothing above it — the strongest model writes no code — so a slice that defeats T3 needs a better plan or a smaller slice, not a stronger writer.

Never escalate on a hunch that the slice "was probably always too hard for that tier". The failure classification step establishes that, not this rubric.
