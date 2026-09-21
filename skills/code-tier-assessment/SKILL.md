---
name: code-tier-assessment
description: classify a code-writer slice into T1/T2/T3 before delegating, using closed-list criteria on file/line scope and criticality/difficulty. use before writing a task brief for code-writer within the task-orchestration workflow.
---

# Code tier assessment

Classify every slice before writing its brief, once you've looked at the affected code — not from the goal statement alone, since you need to know what the change actually touches. Pick the tier from checkable facts about the slice, never from a feeling of difficulty — vague sizing is how tiers drift toward whichever one feels safest.

## The three tiers

| Tier | Agent | Model | Effort | Target share |
|---|---|---|---|---|
| T1 | `code-writer-t1` | haiku | max | ~30% |
| T2 | `code-writer-t2` | sonnet | medium | ~50%, the default |
| T3 | `code-writer-t3` | sonnet | max | ~20% |

`opus` never writes code — it stays reserved for `code-reviewer` and `sql-data-reviewer`. Target shares are a calibration goal, checked against `python3 ~/.claude/tools/first-pass.py` (split by tier), not a quota to force on any single task.

## Default: T2

Start every slice at T2 unless it clearly clears the T1 checklist or trips a T3 trigger. T2 is the safe default for ordinary feature work and most bug fixes — including moderate ones you have not fully sized yet.

## Send to T1 only when every box is checked

- the change is a one-line or small, self-contained edit, or a minor correction (rename, constant, config value, an obvious check the spec already calls for);
- the diff stays under 100 lines in any single file, and under 200 lines in total across every file it touches;
- the slice touches at most 5 files;
- none of the T3 triggers below apply.

Missing any box keeps it at T2 — do not round down because the rest of the task feels easy.

## Send to T3 when any one trigger fires

Criticality — fires regardless of how small the diff looks:

- migrations and schema changes;
- money and billing;
- authn/authz, secrets, permissions;
- external contracts and public APIs;
- deletion or irreversible overwrite of data.

Difficulty and scale:

- no clear approach exists; the slice needs a design decision;
- correctness depends on non-obvious interaction between several subsystems;
- correctness cannot be settled by running a check — it needs an argument about invariants;
- the slice touches more than 15 files, regardless of how small each individual change is.

## Examples

- rename a config key used in 3 files, 40 lines total → T1.
- add a new field end-to-end (model, API, UI) → T2, ordinary feature work.
- add a column via a migration, even if the diff is 5 lines → T3, criticality trigger, not size.

## Naming the tier

Name the tier and, for T1 or T3, the specific box or trigger that put it there, in the brief's `tier` field (see `task-orchestration`'s Task brief). A slice with no named trigger for T1 or T3 defaults to T2 — the lists here are checkable, a hunch is not.

## Escalation

Each tier gets 2 attempts (see `task-orchestration`'s Loop limits). An `executor`-classified failure moves the slice up exactly one tier — T1 to T2, or T2 to T3 — never skipping a tier and never on a hunch that "this was probably always too hard for that tier": the failure classification step is what establishes that, not this rubric.
