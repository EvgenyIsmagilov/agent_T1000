# GDScript (Godot 4.7)

## Style

- **Tabs** for indentation, one level per depth. Continuation lines get 2 extra levels — except arrays, dictionaries, and enums, which get 1.
- Lines under 100 characters, 80 preferred.
- One blank line between functions, two between a class's sections.

| Thing | Case | Example |
| --- | --- | --- |
| files, folders | snake_case | `enemy_spawner.gd` |
| `class_name`, node names | PascalCase | `EnemySpawner` |
| functions, variables, signals | snake_case | `spawn_wave()` |
| signals | snake_case, **past tense** | `wave_cleared` |
| constants | CONSTANT_CASE | `MAX_HEALTH` |
| enums | PascalCase name, CONSTANT_CASE members | `enum State { IDLE, DEAD }` |
| private members | leading underscore | `_recalculate_path()` |

Member order inside a class: `@tool`/`@icon` → `class_name`/`extends` → docstring → signals → enums → constants → static vars → `@export` vars → public vars → private vars → `@onready` vars → `_init` → `_enter_tree` → `_ready` → virtual callbacks → public methods → private methods → inner classes.

## Static typing is the default

Typed GDScript parses faster, runs faster, and catches errors before the game starts. Untyped code is acceptable only where the engine genuinely hands back a `Variant`.

```gdscript
var health: int = 100
var direction := Vector2.RIGHT          # := only when the right side makes the type obvious
@onready var sprite: Sprite2D = $Sprite2D

func take_damage(amount: int, source: Node) -> void:
	pass
```

- Always write the return type, including `-> void`.
- Typed collections: `Array[Enemy]` and, since 4.4, `Dictionary[String, int]`.
- `as` to cast, `is` to test: `var body := collider as CharacterBody2D`, then check `if body:`.
- `@warning_ignore("...")` on the single line that needs it; `@warning_ignore_start`/`@warning_ignore_restore` around a block. Never blanket-disable warnings project-wide to silence one script.

## Nodes

```gdscript
@onready var health_bar: ProgressBar = %HealthBar   # scene-unique name, survives reparenting
@export var target: Node3D                          # wire it in the inspector, not by path
```

- `%Name` beats `$A/B/C` for anything deep or likely to move; `@export` beats both when the node lives in another scene.
- `is_instance_valid(node)` before touching a reference that may have been freed; `node.is_queued_for_deletion()` to avoid double-freeing.
- `queue_free()` in the tree, always. `free()` mid-frame invalidates references the engine is still holding.

## Signals

```gdscript
signal health_changed(old_value: int, new_value: int)

func _ready() -> void:
	health_changed.connect(_on_health_changed)
	enemy.died.connect(_on_enemy_died.bind(enemy), CONNECT_ONE_SHOT)
	health_changed.emit(100, 80)
```

- Declare parameters with types. Emit through the signal object (`sig.emit(...)`), not `emit_signal("...")` — the string form skips type checking.
- `Callable.bind()` appends extra arguments; `unbind()` drops trailing ones.
- Connections die automatically when either object is freed — manual `disconnect()` is for logic changes, not cleanup.
- `await` for one-shot waits: `await enemy.died`, `await get_tree().create_timer(1.0).timeout`, `await tween.finished`.

## Resources and loading

- Data containers: `class_name WeaponStats extends Resource` with `@export` fields, saved as `.tres`.
- `preload()` resolves at parse time (constant path only); `load()` at runtime. Both accept `uid://…`.
- Resources are **shared by default** — two nodes pointing at one `.tres` mutate the same object. Call `duplicate()`, or set `resource_local_to_scene = true` when each instance needs its own copy.
- Large assets: `ResourceLoader.load_threaded_request()` + `load_threaded_get_status()` instead of blocking the main thread.

## Language features by version

- `static var` / `static func`, `@static_unload` — since 4.1.
- `Dictionary[K, V]` typed dictionaries — since 4.4.
- `@abstract` on a class or method — since 4.5. An abstract class cannot be instantiated; a subclass must implement every abstract method or be abstract itself. This replaces the old "empty method + `push_error`" contract pattern.
- `Tween.tween_await()` — since 4.7; pauses a tween until a signal fires (cutscenes, dialogue).

## Annotations worth reaching for

`@export_range`, `@export_enum`, `@export_flags`, `@export_multiline`, `@export_file`/`@export_dir`, `@export_node_path`, `@export_custom`, `@export_storage` (serialized but hidden), `@export_group`/`@export_subgroup`/`@export_category`, `@export_tool_button` (clickable inspector button), `@icon`, `@rpc`, `@tool`.

## Editor tools

`@tool` scripts execute in the editor. Guard anything with side effects:

```gdscript
@tool
extends Node

func _process(delta: float) -> void:
	if Engine.is_editor_hint():
		return
```

A crash in a `@tool` script takes the editor with it. Keep them defensive.

## Errors

- `push_error()` / `push_warning()` for problems a developer must see; `printerr()` for CLI output.
- `assert()` is **stripped from release builds** — never put required logic, side effects, or validation of runtime data inside it.
- Prefer failing loudly over an early `return` that silently does nothing.

## Threads

- `WorkerThreadPool.add_task(callable)` for parallel CPU work; `wait_for_task_completion(id)` to join.
- Never touch nodes, the scene tree, or the rendering server from a worker thread — hop back with `callable.call_deferred()`.
- `Mutex` / `Semaphore` for shared state. If a thread has to touch the tree constantly, it should not be a thread.
