# C# in Godot (4.7)

## Requirements and limits

- Needs a **.NET editor build** (".NET"/Mono download) — the standard build cannot run C#.
- **.NET 8 SDK or later**; Android export requires **.NET 9+**.
- **Web export is not supported for C#** in Godot 4. If the target includes web, that decision belongs to GDScript or a rewrite — raise it before writing code.
- The editor reads compiled assemblies: new `[Export]` fields, `[Signal]`s, and `[GlobalClass]` types do not appear in the inspector until `dotnet build` (or the editor's Build button) succeeds.
- Hot reload preserves nothing except exported variables.

## Style

PascalCase for namespaces, types, methods, properties, constants, events. camelCase for locals and arguments. `_camelCase` for private fields. `I` prefix for interfaces. `var` only when the right-hand side names the type (`var v = new Vector2(1, 0)` yes; `var x = GetValue()` no).

Modifier order: `public`/`protected`/`private`/`internal` → `virtual`/`override`/`abstract`/`new` → `static` → `readonly`.

## Godot-specific rules

```csharp
public partial class Player : CharacterBody2D   // file must be named Player.cs
{
	[Export] public float Speed { get; set; } = 300f;

	[Signal] public delegate void HealthChangedEventHandler(int oldValue, int newValue);

	public override void _Ready()
	{
		HealthChanged += OnHealthChanged;                  // built-in and custom signals are C# events
		GetNode<Timer>("%Cooldown").Timeout += OnCooldown;
	}

	private void Fire() => EmitSignal(SignalName.HealthChanged, 100, 80);
}
```

- `public partial class`, class name identical to the file name. Omitting `partial` breaks the source generators silently.
- Signal delegates must end in `EventHandler`; the generated event drops that suffix.
- Use the generated **StringName caches** — `MethodName.X`, `SignalName.X`, `PropertyName.X` — anywhere the engine wants a name. Raw strings allocate on every call and typos only surface at runtime.
- `Callable.From(() => …)` when an engine API expects a `Callable`; `CallDeferred(MethodName.Foo)` to reach the main thread.
- **Struct properties return copies**: `Position.X = 5;` does not compile. Write `var p = Position; p.X = 5; Position = p;` or `Position = Position with { X = 5 };`.
- `GodotObject`, not `Object`. `QueueFree()`, `GodotObject.IsInstanceValid(node)`.
- `GD.Print` / `GD.PrintErr` / `GD.Randf` — `Console.WriteLine` bypasses the editor output panel.
- `Godot.Collections.Array<T>` / `Dictionary<K,V>` only where the engine API demands them (they marshal on every access). Use `System.Collections.Generic` for internal logic.
- `[Tool]` for editor-run scripts, guarded by `Engine.IsEditorHint()`. `[GlobalClass]` to register the type in the Create Node dialog.
- Nullable reference types fight `[Export]` fields the engine assigns after construction — either enable them project-wide and use `= null!`, or leave them off. Do not mix.

## Performance

- No LINQ, no lambdas capturing state, no string interpolation inside `_Process`/`_PhysicsProcess`.
- Every call across the C#↔engine boundary marshals. Cache node references in `_Ready()`; batch property writes rather than touching the same property repeatedly per frame.
- `Variant` boxing is not free — keep engine-typed values in engine types instead of converting back and forth.
