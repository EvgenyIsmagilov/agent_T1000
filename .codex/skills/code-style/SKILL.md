---
name: code-style
description: language-agnostic code style and hygiene — change size, scope discipline, orphaned and dead code, functions, imports, comments, docstrings, and folder READMEs. use whenever writing, editing, or refactoring code in any language; stack skills (python, sql, godot, docker) apply on top of this one.
---

# Code style

- Prefer small, focused changes over broad rewrites.
- Build only what was requested: no speculative features, options, or abstractions for a single use case, and handle errors that can realistically occur rather than hypothetical ones. Litmus test: would a senior engineer call this overcomplicated?
- Do not make unrelated refactors. Don't reformat or "improve" lines you didn't have to touch.
- When a change orphans code, clean up only your own mess — remove only the imports and helpers your change made unused. Flag unrelated dead code instead of deleting it.
- Follow the repository's existing patterns.
- Prefer small, pure functions.
- Do not use wildcard imports.

## Comments and documentation

- A comment explains why the code is the way it is, not what it does. Do not comment code that is already clear.
- If code is a workaround, add a link to the issue, documentation, or bug that explains it.
- Do not leave commented-out code in your changes; Git keeps the history. Flag commented-out code you did not write instead of deleting it.
- For a public API, the docstring (or XML doc in C#) states when to call it, any non-obvious behavior, and edge cases. An internal helper needs one line or no docstring.
- A folder README starts with what the folder owns and the boundary that later edits must keep. Do not list every file or repeat what file names already say.
