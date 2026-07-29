---
name: godot
description: godot game engine coding standards — gdscript and c#. use for .gd, .tscn, and .tres files, godot projects, gameplay code, and godot performance or profiling work.
---

# Godot

Apply these standards when working on a Godot project.

## Workflow

1. Inspect the project's Godot version (3.x and 4.x differ significantly), language (GDScript or C#), and existing structure and conventions.
2. Follow the project's conventions — the project always wins over defaults here.
3. Make the smallest focused change and match the surrounding node/scene patterns.
4. Verify in-editor or with a focused run where possible.

## Architecture

- Compose scenes from small, single-purpose nodes. Prefer composition over deep inheritance.
- "Call down, signal up": a parent may call child methods, but children communicate upward through signals rather than reaching into parents.
- Keep scenes self-contained and instanceable. Avoid hardcoded absolute node paths.
- Use autoloads (singletons) only for genuinely global state; do not overuse them.
- Store tunable data in Resources (`.tres`) instead of hardcoding values.

## GDScript style

- Follow the official GDScript style guide.
- `snake_case` for functions, variables, and signals; `PascalCase` for `class_name` and node names; `CONSTANT_CASE` for constants.
- Use static typing (typed GDScript) for parameters, returns, and variables — it aids clarity and performance.
- Match the version's syntax: Godot 4 uses `@export` / `@onready` / `await`; Godot 3 uses `export` / `onready` / `yield`.
- Cache node references in `@onready` instead of repeated `get_node()` calls.
- Expose tunable values with `@export` rather than leaving magic numbers in code.

## Performance

- Keep `_process()` and `_physics_process()` lean — they run every frame. Move one-off work out of them.
- Put physics and movement in `_physics_process` (fixed step, scale by `delta`); put visual/frame logic in `_process`.
- Avoid per-frame allocations and node instantiation; pool frequently spawned objects.
- Free nodes with `queue_free()`, not `free()`.
- Avoid `get_node()` / `find_child()` in hot paths; cache references. Prefer signals or timers over polling in `_process`.
- Profile with the Godot profiler and monitors before optimizing — do not guess.

## Validation

- Confirm the project opens without errors and the script has no parse or type errors.
- Test the affected scene in-editor or via a focused run when possible.
- Never claim it runs unless it was actually executed. State what was and was not verified.
