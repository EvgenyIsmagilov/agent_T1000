# agent_T1000

Personal agent configuration — instructions, subagents, skills, and
orchestration hooks — for three coding-agent CLIs, kept in sync by hand.

- [`claude/`](claude/) — Claude Code (`~/.claude`): `CLAUDE.md`, `agents/`,
  `skills/`, hook scripts under `hooks/`, `commands/`, `tools/`.
- [`codex/`](codex/) — Codex (`~/.codex`): `AGENTS.md`, `agents/*.toml`,
  `skills/`.
- [`opencode/`](opencode/) — OpenCode (`~/.config/opencode`): `agent/`,
  skill overrides, the `claude-hooks.js` plugin, `tools/`. See
  [`opencode/README.md`](opencode/README.md) for what's deliberately not
  copied (local provider config) and known gaps.

The three don't share a file format — each tool has its own conventions for
agents, skills, and hooks — so the same orchestration doctrine
(`task-orchestration` skill, executor tiers, the completion gate) is
maintained as three separate, tool-appropriate copies rather than one shared
source. When one changes, the others are updated by hand; drift between them
is a known risk, not an assumption that they're identical.

Paths, local IPs, and personal identifiers are stripped before anything lands
here — see each hook file for the environment-variable convention used in
place of a hardcoded local path.

## Personal files and links per harness

Some instructions are personal (for example, the tone of voice) and are kept
out of this repository. They live in a local file that each tool loads in its
own way, because the tools do not share an include mechanism:

| Tool | How an external file is pulled in | Notes |
|---|---|---|
| Claude Code | `@path/to/file` line in `CLAUDE.md`, expanded at launch | Relative and absolute paths, `~` allowed, up to four nested hops. Imports in the user-level `~/.claude/CLAUDE.md` load without an approval dialog. A path inside backticks is not imported. |
| OpenCode | `instructions` array in `opencode.jsonc` (paths, globs, URLs) | `@file` references in `AGENTS.md`/`CLAUDE.md` are **not** expanded; the text stays literal. Use an absolute path: `~` expansion in `instructions` is not confirmed by the docs. |
| Codex | none | The Codex docs describe no include syntax (an `@include` request is open upstream), so Codex does not get the personal file at all. |
| Hermes | `~/.hermes/SOUL.md` | Hermes loads this file as the first part of its system prompt; it is a symlink to the personal file. Not verified by running Hermes. |

Sources: the memory docs of Claude Code, the rules page of OpenCode, and the
`AGENTS.md` guide of Codex, read on 2026-10-02. Not verified by running the
tools: whether OpenCode's `external_directory` permission affects
`instructions` pointing outside the project, and whether Hermes
follows the symlink in every mode.

A local pre-commit/pre-push guard refuses commits that contain the personal text.
