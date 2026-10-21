# agent_T1000

Agent configuration — instructions, subagents, skills, and orchestration
hooks — for four coding-agent harnesses, kept in sync by hand.

Each top-level directory has the same name as the directory the harness reads
on a real machine, so a file here maps to one path under `$HOME`:

| Directory here | Harness | Real location | Contents |
|---|---|---|---|
| [`.claude/`](.claude/) | Claude Code | `~/.claude` | `CLAUDE.md`, `agents/`, `skills/`, `commands/`, `hooks/`, `tools/`, `docs/`, `settings.json` |
| [`.codex/`](.codex/) | Codex | `~/.codex` | `AGENTS.md`, `agents/*.toml`, `skills/` |
| [`.config/opencode/`](.config/opencode/) | OpenCode | `~/.config/opencode` | `agent/`, skill overrides, `plugins/claude-hooks.js`, `lib/`, `tools/` |
| [`.dsh/`](.dsh/) | DeepSeek Harness (DSH) | `~/.dsh` | `AGENTS.md`, `skills-local/` |

See [`.config/opencode/README.md`](.config/opencode/README.md) and
[`.dsh/README.md`](.dsh/README.md) for what each harness does not get and why.

The harnesses do not share a file format — each tool has its own conventions
for agents, skills, and hooks — so the same orchestration doctrine
(`task-orchestration` skill, executor tiers, the completion gate) is
maintained as separate, tool-appropriate copies rather than one shared
source. When one changes, the others are updated by hand; drift between them
is a known risk, not an assumption that they are identical.
`.claude/tools/harness-check.py` reports some of the drift.

## What is not here, on purpose

- Persona and tone. The persona file is personal and is not in this
  repository. [`.claude/persona.example.md`](.claude/persona.example.md) is a
  neutral template: copy it to `~/.claude/persona.md` and edit it.
- Provider and model wiring (`opencode.jsonc`, Codex `config.toml`, DSH
  `cordis.patch.yml`), credentials, session history, and memory.
- Machine-specific values. A path or a host that is specific to one machine is
  replaced by a placeholder or an environment variable. Search for `USER` in
  `.claude/hooks/dsh-hooks.json` and `TRINO_GUARDRAIL_PROJECT_ROOT` in
  `.claude/hooks/trino-guardrail.py`.
- Skills that came from other authors or from a harness bundle. Only the
  skills written for this setup are copied.

## Personal files and links per harness

Personal instructions live in a local file that each tool loads in its own
way, because the tools do not share an include mechanism:

| Tool | How an external file is pulled in | Notes |
|---|---|---|
| Claude Code | `@path/to/file` line in `CLAUDE.md`, expanded at launch | Relative and absolute paths, `~` allowed, up to four nested hops. Imports in the user-level `~/.claude/CLAUDE.md` load without an approval dialog. A path inside backticks is not imported. |
| OpenCode | `instructions` array in `opencode.jsonc` (paths, globs, URLs) | `@file` references in `AGENTS.md`/`CLAUDE.md` are **not** expanded; the text stays literal. Use an absolute path: `~` expansion in `instructions` is not confirmed by the docs. |
| Codex | none | The Codex docs describe no include syntax (an `@include` request is open upstream), so Codex does not get the personal file at all. |
| DSH | none | DSH does not expand `@path`. `.claude/tools/gen_dsh.py` writes `~/.dsh/AGENTS.md` with the imported text already inlined. |

Sources: the memory docs of Claude Code, the rules page of OpenCode, and the
`AGENTS.md` guide of Codex, read on 2026-10-02. Not verified by running the
tools: whether OpenCode's `external_directory` permission affects
`instructions` pointing outside the project.

A local pre-push guard (`.claude/tools/persona-guard.py`, wired by
`.claude/tools/githooks/pre-push`) refuses pushes that contain the personal
text. It needs `~/.claude/persona.md` and `~/.claude/persona-markers.txt` to
exist; both files are listed in `.gitignore`.
