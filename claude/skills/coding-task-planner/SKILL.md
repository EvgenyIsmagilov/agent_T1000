---
name: coding-task-planner
description: Turn a high-level software-development request into a repository-grounded Markdown task specification for another coding agent. Use when an agent must plan or hand off implementation work, investigate an existing codebase before assigning work, clarify ambiguous product or engineering requirements, define verifiable acceptance criteria and tests, or prepare a self-contained task for a code-writing agent. Use this even for a casual one-line ask like "write up a task for the coding agent" or "turn this into a ticket for implementation" — not only when the user explicitly requests a formal specification.
---

# Coding Task Planner

Transform a rough development request into a self-contained implementation task that another coding agent can execute with minimal follow-up.

Optimize for **handoff quality**, not for writing code. Describe the problem and observable outcome more precisely than the implementation.

## Core rules

1. **Inspect the repository before drafting the task or questioning the user about implementation context.**
2. **Do not implement the feature, modify production code, or turn the task into a patch.** The deliverable is the task specification.
3. **Do not ask the user for facts that can reasonably be discovered from the repository, tests, config, documentation, history, or nearby implementations.**
4. **Do ask the user when a decision depends on product intent, desired behavior, scope, UX semantics, compatibility promises, or another fact the repository cannot determine.**
5. **Never fabricate repository facts.** Distinguish confirmed findings from likely relevance or assumptions.
6. **Treat user requirements as authoritative unless they conflict with each other or with an explicit technical constraint.** Surface conflicts instead of silently resolving them.
7. **Prefer problem specification over solution prescription.** Constrain implementation only when the constraint is real and relevant.
8. **Every acceptance criterion must be independently verifiable.** Avoid words such as "properly", "correctly", "cleanly", "robustly", or "as expected" without defining observable behavior.
9. **Separate hard requirements from recommendations.** A suggestion from the planner must never masquerade as a requirement.
10. **Keep scope tight.** Explicitly state what is out of scope when adjacent work could tempt the coding agent into unnecessary refactoring.
11. **Use exact repository paths only after verifying them.** If a file is only probably relevant, label it `Likely relevant` rather than presenting it as fact.
12. **Discover verification commands from the repository.** Do not invent build, test, lint, format, or run commands.

## Workflow

Follow these steps in order.

### Step 1: Parse the high-level request

Extract the apparent goal, requested behavior, known constraints, explicit exclusions, and terminology used by the user.

Do not immediately convert the request into a task. Treat the first interpretation as a hypothesis to validate against the repository.

### Step 2: Investigate the repository

Explore enough of the repository to understand the existing behavior and likely change surface.

At minimum, look for:

- repository/module structure;
- entry points related to the requested behavior;
- classes, interfaces, services, scenes, components, data models, APIs, or commands involved;
- existing tests around the behavior;
- configuration and feature flags;
- similar or neighboring implementations that establish local patterns;
- public interfaces or serialized/persisted data that may impose compatibility constraints;
- build, test, lint, format, and run commands actually used by the project;
- documentation or comments that materially affect the task.

Trace behavior far enough to answer **how the current system behaves**, not merely where files with matching names are located.

For Godot/C# repositories, inspect relevant `project.godot`, `.csproj` files, scenes/resources, node ownership/lifecycle, autoloads, signals/events, serialized resources, and existing C# conventions when they are part of the change path.

If no related code exists yet (new project or subsystem), skip investigation for what doesn't exist, state this explicitly in `Context`, and treat any assumed stack, framework, or pattern as a blocking question rather than an invented fact.

### Step 3: Build an ambiguity ledger

After repository investigation, identify unresolved questions that could materially change one or more of:

- user-visible behavior;
- scope;
- data contracts or persistence;
- error/failure behavior;
- compatibility;
- lifecycle or timing;
- ownership of state;
- acceptance criteria;
- test expectations.

Classify each ambiguity internally as:

- **Blocking** — different answers would produce meaningfully different tasks. Ask the user before finalizing.
- **Non-blocking** — a conservative assumption is safe. Record the assumption in `Context` and continue.

Read `references/questioning.md` when blocking ambiguities remain.

### Step 4: Question the user only where necessary

Ask a small, prioritized batch of high-impact questions. Prefer questions that expose hidden assumptions and force ambiguous behavior to become explicit.

Rules:

- Ask about **intent and behavior**, not code trivia discoverable from the repository.
- Prefer concrete scenarios and choices over broad questions such as "How should it work?".
- When useful, state the consequence of each choice.
- Challenge contradictions politely and explicitly.
- Do not overwhelm the user with every imaginable edge case. Prioritize decisions that change implementation or acceptance criteria.
- If the user already answered a question, do not ask it again.

If no blocking ambiguity remains, proceed without questions.

### Step 5: Define the task boundary

Before writing the final task, decide:

- the smallest coherent behavior change that satisfies the request;
- what existing behavior must remain unchanged;
- which technical constraints are real versus merely preferred;
- which adjacent refactors or enhancements are out of scope;
- how success can be observed and verified.

Do not inflate a feature request into an architecture rewrite unless the repository makes that unavoidable.

### Step 6: Write the final Markdown task

Once blocking questions are resolved, output **only** the Markdown task. Do not prepend commentary, analysis, or a summary to the coding agent.

Use exactly this top-level structure:

```markdown
# Task: <concise implementation-oriented title>

## 1. Goal
<What outcome must be achieved and why. Keep this short.>

## 2. Context
<Repository-grounded context needed to understand the task. Include explicit assumptions only when needed.>

## 3. Current behavior
<What the system does today, based on repository evidence.>

## 4. Desired behavior
<Observable behavior after implementation. Describe scenarios and state transitions clearly.>

## 5. Relevant code/files
| Path | Role | Relevance | Expected impact |
| --- | --- | --- | --- |
| `path/to/file` | <what it does> | Confirmed relevant / Likely relevant | <why it may need to change or remain compatible> |

## 6. Implementation constraints
- **Requirement:** <non-negotiable product or technical constraint>
- **Constraint:** <existing architectural/API/compatibility limitation>
- **Recommendation:** <optional approach that appears consistent with the codebase>
- **Out of scope:** <adjacent work the coding agent must not expand into>

## 7. Acceptance criteria / Definition of Done
### Acceptance criteria
- [ ] <specific observable and verifiable outcome>
- [ ] <specific observable and verifiable outcome>

### Definition of Done
- [ ] <quality/compatibility condition>
- [ ] <build/test/lint/regression condition grounded in the repo>

## 8. Tests
### Existing coverage
<Relevant tests or state that none were found.>

### Required coverage
- <test case and expected result>

### Verification commands
```text
<commands verified from repository documentation/configuration>
```

## 9. Edge cases
- <relevant boundary/failure/lifecycle scenario and required behavior>
```

Do not add extra top-level sections. Put assumptions in `Context`, exclusions in `Implementation constraints`, and verification commands in `Tests`.

## Section quality rules

### Goal

State one coherent outcome. Avoid implementation details unless the implementation itself is the requirement.

Bad:

`Create an InventoryManager singleton with a Dictionary.`

Better:

`Allow the player's inventory state to persist across scene changes without duplicating items.`

### Context

Include only information that helps the coding agent reason correctly. Prefer repository evidence over generic architecture commentary.

When using an assumption, label it explicitly:

`Assumption: ...`

Never hide uncertainty inside confident prose.

### Current behavior

Describe what actually happens today. Ground material claims in paths, symbols, tests, or configuration discovered during investigation.

Do not confuse an apparent code path with proven runtime behavior. If evidence is incomplete, say so.

### Desired behavior

Describe observable states, triggers, outputs, side effects, failure behavior, and unchanged behavior where relevant.

Prefer scenario language such as:

`When X happens, Y must occur; Z must remain unchanged.`

### Relevant code/files

Include only files with meaningful relevance. Do not dump search results.

Use:

- `Confirmed relevant` when the current behavior or integration path was traced through the file.
- `Likely relevant` when evidence suggests involvement but the exact implementation choice belongs to the coding agent.

A file may be relevant even if it should **not** be modified; note compatibility expectations in `Expected impact`.

### Implementation constraints

Keep four concepts distinct:

- **Requirement** — demanded behavior or explicit user requirement.
- **Constraint** — limitation imposed by existing architecture, API, data, platform, compatibility, or tooling.
- **Recommendation** — planner guidance the coding agent may deviate from with a better justified solution.
- **Out of scope** — work intentionally excluded from this task.

Do not convert a familiar pattern into a requirement merely because it exists elsewhere in the codebase.

### Acceptance criteria / Definition of Done

Acceptance criteria describe **behavioral success**. Definition of Done describes **implementation quality and completion conditions**.

Each criterion must be testable by a human, automated test, command, or reproducible scenario.

Prefer concrete Given/When/Then semantics when behavior is conditional, but do not force ceremonial wording when a direct statement is clearer.

### Tests

First discover the project's existing testing style. Reuse its conventions where practical.

Cover the behavior that can regress, not implementation trivia. Include positive paths, important negative paths, and relevant lifecycle/state transitions.

List only commands verified from repository files or tooling. If no automated verification command exists, say so and provide a precise manual verification scenario instead of inventing one.

### Edge cases

Include plausible edge cases that affect correctness. Do not pad the section with generic possibilities unrelated to the feature.

For each meaningful edge case, specify the expected behavior when it is known. If the expected behavior is a product decision and remains ambiguous, return to the questioning step rather than guessing.

## Final self-review

Before emitting the task, verify all of the following:

- Another coding agent can start work without reconstructing the task from scratch.
- Current behavior is based on repository investigation rather than assumption.
- No question remains whose answer would materially change the task.
- The task specifies the problem more precisely than the solution.
- Hard requirements and recommendations are clearly distinguishable.
- Acceptance criteria are observable and verifiable.
- Definition of Done is not a duplicate of acceptance criteria.
- Relevant file paths were actually verified or explicitly marked `Likely relevant`.
- Tests reflect repository conventions and verification commands are real.
- Important edge cases are covered without speculative scope creep.
- Out-of-scope boundaries prevent tempting but unnecessary adjacent work.
- The final output contains exactly the nine required top-level sections.

If any check fails, fix the task before outputting it.
