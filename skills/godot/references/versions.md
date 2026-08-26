# Versions and migration (as of August 2026)

## Where the engine stands

| Branch | Status |
| --- | --- |
| 4.7 (4.7.1 latest patch, released 14 Jul 2026; 4.7 released 18 Jun 2026) | current stable, full support |
| 4.6 (4.6.3) | full support |
| 4.5 | security and platform fixes only |
| 4.8 | in development |
| 3.6 | LTS, 3.7 in development |

New projects go on 4.7. Existing 4.x projects should take patch releases (4.7.0 → 4.7.1) without hesitation; minor upgrades (4.6 → 4.7) need a re-import and a test pass.

## What landed when (code-relevant only)

**4.4** — UIDs generalized to all resources, with `.uid` sidecar files for scripts and plain-text resources; typed dictionaries `Dictionary[K, V]`; Jolt physics integrated into the engine as a built-in option.

**4.5** — `@abstract` for abstract classes and methods.

**4.6** — **Jolt becomes the default 3D physics engine for new projects** (existing projects keep their setting). Nodes gain a unique internal ID so the engine tracks them across scene refactors — re-save all scenes via *Project → Tools → Upgrade Project Files* to benefit. GDExtension's interface moves from a C header to a JSON definition, and API parameters/returns can be declared required (nullables no longer implicitly allowed). External tracing profilers supported (Tracy, Perfetto, Instruments). Signals starting with `_` no longer show in autocomplete.

**4.7** — HDR output (Windows, macOS, iOS, visionOS, Linux/Wayland). `Control` gains `offset_transform_*` so a control can be translated/rotated/scaled without disturbing its container. `AreaLight3D` for rectangular area lights. `Tween.tween_await()`. `DrawableTexture2D`. `VirtualJoystick` node. `CollisionShape2D.one_way_collision_direction`. `GradientTexture2D` conic fill. `InputEvent` constants `DEVICE_ID_KEYBOARD` / `DEVICE_ID_MOUSE`. Standalone Android exporting; Perfetto is the default profiler on Android. Java interfaces implementable from GDScript via `JavaClassWrapper`. New Asset Store replaces the old asset library workflow.

**4.8 (upcoming)** — `GodotPhysics3D` is planned for removal to cut binary size; it would return only as an extension. Treat any new 3D physics work as Jolt-targeted.

## Jolt vs GodotPhysics3D

Switching `physics/3d/physics_engine` is a behavioral change, not a toggle. Known differences:

- Soft limit properties on `PinJoint3D`, `HingeJoint3D`, `SliderJoint3D`, `ConeTwistJoint3D`, `Generic6DOFJoint3D` are unsupported; Jolt warns when they are set.
- Single-body joints treat "the world" differently and can invert limits — there is a "World Node" compatibility setting.
- Collision margins shrink the shape before applying the shell (Godot Physics only expands); tune via "Collision Margin Fraction".
- `Area3D` reports overlaps with `SoftBody3D` by default — opt out via collision masks.
- Contact impulses are estimates, accurate only for two-body collisions.
- Kinematic body contact reporting and ray-cast face indices are opt-in (the latter costs ~25% more memory).

Migrating an existing project: change the setting, then re-test every joint, one-way platform, and anything reading contact impulses. Say so explicitly rather than assuming parity.

## Godot 3 → 4

Only relevant when a project is genuinely on 3.x. The syntax split is total: `export`/`onready`/`yield` → `@export`/`@onready`/`await`, `KinematicBody` → `CharacterBody`, `instance()` → `instantiate()`, `connect("sig", self, "_on")` → `sig.connect(_on)`, and the whole rendering/physics stack changed. Do not half-port a file; port a scene at a time and run it.
