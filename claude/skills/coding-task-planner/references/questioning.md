# Requirement Questioning Guide

Use this guide only after repository investigation leaves blocking ambiguity.

The purpose is not to conduct a generic interview. The purpose is to expose decisions that another coding agent cannot safely infer.

## Principles

### Repository first

Before asking a question, ask yourself:

`Could I answer this by reading more of the repository, tests, config, docs, or a neighboring implementation?`

If yes, investigate instead of asking the user.

### Attack ambiguity, not the user

Challenge vague requirements by testing them against concrete scenarios.

Instead of:

`How should saving work?`

Prefer:

`If the player closes the game immediately after changing this value, must the new value survive the next launch, or is persistence only required for the current session?`

### Prefer decisions over essays

When the decision space is small, give explicit options and explain why the answer matters.

Example:

`When the target is already active, should a second activation: (A) do nothing, (B) refresh the duration, or (C) stack another instance? This changes both state handling and acceptance tests.`

Do not force options when the user's intent may fall outside them; allow an `other` answer implicitly.

### Probe failure semantics

Many vague tasks define only the happy path. Ask what must happen when inputs, dependencies, state, timing, or external systems are invalid when that materially changes behavior.

Useful probes:

- What if the action is repeated?
- What if required state is missing?
- What if the operation is interrupted halfway through?
- What if old persisted data is loaded?
- What if two events happen in the opposite order?
- What if the same operation happens twice?
- What should remain unchanged when the operation fails?

Ask only those relevant to the actual feature.

### Probe boundaries

Clarify what the task should *not* solve when adjacent scope is plausible.

Examples:

- Is migration of old data required?
- Is editor tooling part of this task?
- Are UI changes required or only underlying behavior?
- Must this support existing save files/API clients/scenes?
- Is multiplayer/server authority part of the change?

### Probe observable behavior

Translate adjectives into measurable outcomes.

If the user says:

- `fast` — ask about acceptable latency or scale only if performance is material.
- `safe` — ask what failure must be prevented.
- `smooth` — ask what the user should actually observe.
- `support X` — ask which actions or scenarios constitute support.
- `same as Y` — inspect Y first, then ask only about intentional differences.

## Priority order

Ask about ambiguities in this order:

1. Contradictory requirements.
2. User-visible behavior with multiple plausible interpretations.
3. Data loss, persistence, compatibility, or destructive behavior.
4. Scope boundaries that could multiply implementation size.
5. Failure and retry semantics.
6. Important lifecycle/timing/concurrency behavior.
7. Performance targets that materially affect design.
8. Cosmetic or low-impact preferences.

Do not block finalization on low-impact preferences when a conservative assumption is safe.

## Question batch rules

- Ask the smallest batch that can unblock the task.
- Prefer 3-5 high-impact questions; do not exceed 7 in one round unless the request is unusually complex.
- Order questions by impact.
- For each question, make the ambiguity concrete.
- Mention why the answer matters when that is not obvious.
- Do not ask the same question in different wording.
- Do not ask the user to choose an implementation detail unless it is genuinely a requirement.

## Contradiction handling

When user intent conflicts with repository reality, distinguish these cases:

### Requirement conflicts with current behavior

This is usually fine: the task may intentionally change current behavior. State the difference clearly.

### Requirement conflicts with another requirement

Ask the user to choose or define precedence. Do not invent priority.

### Requirement conflicts with a hard platform/API constraint

Explain the concrete conflict and ask which requirement may be relaxed. Do not disguise the limitation as an implementation preference.

### User proposes a specific implementation that appears unnecessary or harmful

Treat the desired outcome as primary unless the implementation itself is explicitly mandatory. Ask whether the implementation is a hard constraint, and briefly explain the repository-grounded reason for questioning it.

## Assumptions

Use an assumption without asking only when all are true:

- the ambiguity is non-blocking;
- the conservative choice is obvious enough;
- choosing differently would not materially change architecture, scope, public behavior, or acceptance criteria;
- the assumption can be stated explicitly in `Context`.

If these conditions are not met, ask.
