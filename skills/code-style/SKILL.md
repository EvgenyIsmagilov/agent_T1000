---
name: code-style
description: language-agnostic code style and hygiene — change size, scope discipline, orphaned and dead code, functions, imports. use whenever writing, editing, or refactoring code in any language; stack skills (python, sql, godot, docker) apply on top of this one.
---

# Code style

- Prefer small, focused changes over broad rewrites.
- Build only what was requested: no speculative features, options, or abstractions for a single use case, and handle errors that can realistically occur rather than hypothetical ones. Litmus test: would a senior engineer call this overcomplicated?
- Do not make unrelated refactors. Don't reformat or "improve" lines you didn't have to touch.
- When a change orphans code, clean up only your own mess — remove only the imports and helpers your change made unused. Flag unrelated dead code instead of deleting it.
- Follow the repository's existing patterns.
- Prefer small, pure functions.
- Do not use wildcard imports.
