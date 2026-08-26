---
name: godot
description: godot 4.x engine standards — gdscript, c#, scenes, resources, physics, shaders. use for .gd/.tscn/.tres/.gdshader files, project.godot, godot gameplay or editor-tool code, godot profiling and performance, and godot version migration, export, or ci setup.
---

# Godot

Baseline: **Godot 4.7** (current stable line as of August 2026; 4.7.1 is the latest patch).
Support status: 4.7 and 4.6 fully supported, 4.5 security/platform fixes only, 3.6 is the LTS branch.

The project's own version and conventions outrank every default in this skill.

## Workflow

1. **Read the version first, before writing a line.** `project.godot` → `config/features=PackedStringArray("4.7", "Forward Plus")` gives the engine version *and* the renderer. A `.csproj`/`.sln` in the root means a .NET build (GDScript still works alongside C#).
2. Map the existing conventions: folder layout, autoloads, naming, how scenes talk to each other. Copy them.
3. Make the smallest focused change; reuse the node/scene patterns already present.
4. Validate with the checks below, then state precisely what ran and what did not.

Language details: [`references/gdscript.md`](references/gdscript.md) · [`references/csharp.md`](references/csharp.md).
When the version matters (feature availability, upgrade, deprecation): [`references/versions.md`](references/versions.md).

## Architecture

- Compose scenes from small, single-purpose nodes. Composition over deep inheritance.
- **Call down, signal up**: a parent may call into children; children report upward with signals and never reach into a parent.
- Keep every scene instanceable in isolation. No absolute node paths (`/root/Main/...`) — use `$Child`, `%UniqueName`, or an `@export`ed reference.
- Autoloads carry genuinely global state only (save data, audio bus, event bus). Each one is a hidden dependency for every scene.
- Tunables live in `Resource` files (`.tres`) or `@export` fields, not in literals inside logic.
- Reference assets by UID (`uid://…`, copied from the FileSystem dock) rather than `res://` paths — UIDs survive moves and renames. Commit the `.uid` sidecar files.

## Physics and rendering defaults

- **Jolt is the default 3D physics engine** for new projects since 4.6, and `GodotPhysics3D` is slated for removal in 4.8. Target Jolt for new 3D work; check `physics/3d/physics_engine` in project settings before assuming. Jolt differs in joint limits, collision margins, and contact impulse accuracy — see [`references/versions.md`](references/versions.md).
- Renderer (Forward+ / Mobile / Compatibility) is a project-wide decision with different feature sets. Never switch it to "fix" something without saying what it breaks.
- Movement and physics queries go in `_physics_process` (fixed step, scale by `delta`); visual and input-polling work goes in `_process`.

## Performance

- Treat `_process`/`_physics_process` as a budget: no allocations, no `get_node()`, no `find_child()`, no string building. Cache references in `@onready`.
- Turn callbacks off when idle: `set_process(false)`, `set_physics_process(false)`, `set_process_unhandled_input(false)`.
- Prefer signals, timers, and `Area` callbacks over polling state every frame.
- Pool frequently spawned objects instead of `instantiate()`/`queue_free()` churn. Free with `queue_free()`, never `free()`, on anything in the tree.
- The scene tree is main-thread-only. Offload CPU work to `WorkerThreadPool` and come back via `call_deferred()`.
- Measure before optimizing: built-in profiler and monitors first; since 4.6 the engine also supports external tracing (Tracy, Perfetto, Instruments) for frame-level analysis. Report numbers, not hunches.

## Validation

Run from the project root; `--headless` is required on machines without a GPU (CI).

```bash
godot --headless --path . --check-only --script res://path/to/script.gd
```

```bash
godot --headless --path . --quit res://scenes/main.tscn
```

- `--check-only` parses one script for syntax/type errors. `--quit` boots a scene for a single iteration — the cheapest smoke test that `_ready()` survives.
- A zero exit code is not proof: scan stderr for `SCRIPT ERROR`, `ERROR:`, and `Parse Error`.
- For C#, `dotnet build` must pass *before* anything editor-facing — the editor loads compiled assemblies, so new exports and signals do not exist until the build succeeds.
- Test/lint/CI commands and VCS hygiene: [`references/tooling.md`](references/tooling.md).
- Say "not verified" when it was not run. Never claim a scene runs unless it was actually launched.
