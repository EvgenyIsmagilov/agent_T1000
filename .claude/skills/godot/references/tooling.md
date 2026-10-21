# Tooling, tests, and version control

## Command line

Run from the project root, or pass `--path <dir>`.

| Goal | Command |
| --- | --- |
| Parse one script, no run | `godot --headless --path . --check-only --script res://player.gd` |
| Boot a scene for one iteration | `godot --headless --path . --quit res://scenes/main.tscn` |
| Import/reimport all assets | `godot --headless --path . --import` |
| Export | `godot --headless --path . --export-release "Linux" build/game.x86_64` |
| Run a plain script | `godot --headless --path . -s res://tools/gen_data.gd` |

`--headless` is mandatory on machines without GPU access (CI runners). Always `--import` on a fresh checkout before exporting — `.godot/` is not in version control, so the first run has no import cache.

Exit codes lie: grep stderr for `SCRIPT ERROR`, `ERROR:`, `Parse Error`, and `WARNING:` before calling a run clean.

## Linting and formatting (GDScript)

`gdtoolkit` (pip: `gdtoolkit`, latest 4.5.0) provides `gdformat` and `gdlint`. It is a third-party parser that tracks the language independently and has lagged behind newly added syntax (`@abstract`, for one). Adopt it only after confirming it parses the project's actual scripts, and pin the version in CI.

```bash
gdformat --check $(find . -name '*.gd' -not -path './addons/*')
```

Skip `addons/` — third-party code should not be reformatted.

## Tests

- **gdUnit4** — Godot 4.3+, GDScript and C#, runs headless via CLI, emits JUnit XML and HTML reports, ships an official GitHub Action. Default choice for anything that needs CI.
- **GUT** — GDScript only; check its declared Godot compatibility before adopting.
- Prefer testing pure logic (Resources, plain classes, autoload state machines) over driving scenes. Scene tests need `--headless` plus a real scene tree, and they are slow.

## CI outline

1. Cache `~/.local/share/godot` and the project `.godot/` between runs; import is the expensive step.
2. `godot --headless --path . --import`
3. `dotnet build` for C# projects.
4. Run the test suite headless.
5. Export only on tags/releases — export templates are a large download.

## Version control

- **Ignore**: `.godot/` (cache), `*.translation` (generated from CSV), build/export output directories, `.mono/` and `bin/`/`obj/` for C#.
- **Commit**: `.import` files, `.uid` sidecars (losing them breaks every reference by UID), `project.godot`, `export_presets.cfg` — but check it for credentials first on projects that started on 3.x/4.0, where secrets were stored there.
- Godot writes mergeable text formats. Keep `.tscn`/`.tres` as text, never switch to binary just to shrink the repo.
- Git LFS for `.fbx`, `.glb`, `.blend`, `.png`, `.wav`, and other binary assets; set LF line endings for text in `.gitattributes`.
- Scene merge conflicts are usually resolvable by hand, but a conflicting node ID or `[ext_resource]` block is a signal to re-save the scene from the editor rather than hand-patch it.
