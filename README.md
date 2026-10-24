# agent_T1000

This repository holds the agent configuration for four coding-agent harnesses. It has instructions, subagents, skills, and orchestration hooks. The owner keeps the four sets in sync by hand.

Each top-level directory has the same name as the directory that its harness reads. A file here maps to one path under `$HOME`.

| Directory here | Harness | Real location | Contents |
|---|---|---|---|
| [`.claude/`](.claude/) | Claude Code | `~/.claude` | `CLAUDE.md`, `agents/`, `skills/`, `commands/`, `hooks/`, `tools/`, `docs/`, `settings.json` |
| [`.codex/`](.codex/) | Codex | `~/.codex` | `AGENTS.md`, `agents/*.toml`, `skills/` |
| [`.config/opencode/`](.config/opencode/) | OpenCode | `~/.config/opencode` | `agent/`, skill overrides, `plugins/claude-hooks.js`, `lib/`, `tools/` |
| [`.dsh/`](.dsh/) | DeepSeek Harness (DSH) | `~/.dsh` | `AGENTS.md`, `skills-local/` |

Read [`.config/opencode/README.md`](.config/opencode/README.md) and [`.dsh/README.md`](.dsh/README.md). They list what each harness does not get, and why.

## Why there are separate copies

The harnesses use different file formats. Each tool has its own rules for agents, skills, and hooks. For this reason the repository keeps separate copies of the orchestration rules. These rules are the `task-orchestration` skill, the executor tiers, and the completion gate.

When one copy changes, update the other copies by hand. The copies can differ. Do not assume that they are identical. The script `.claude/tools/harness-check.py` reports some of the differences.

## What is not in this repository

- Persona and tone. The persona file is personal. Copy [`.claude/persona.example.md`](.claude/persona.example.md) to `~/.claude/persona.md` and edit the copy.
- Provider and model settings. These are `opencode.jsonc`, the Codex `config.toml`, and the DSH `cordis.patch.yml`.
- Credentials, session history, and memory.
- Values that belong to one machine. A placeholder or an environment variable replaces each of them. Search for `USER` in `.claude/hooks/dsh-hooks.json`. Search for `TRINO_GUARDRAIL_PROJECT_ROOT` in `.claude/hooks/trino-guardrail.py`.
- Skills from other authors or from a harness bundle. The repository has only the skills that the owner wrote for this setup.

## Personal files in each harness

Personal instructions stay in a local file. Each tool loads that file in a different way, because the tools have no shared include method.

| Tool | Method | Notes |
|---|---|---|
| Claude Code | Put a line `@path/to/file` in `CLAUDE.md`. The tool expands it at launch. | Relative paths, absolute paths, and `~` are allowed. The limit is four nested imports. A path in backticks is not imported. |
| OpenCode | Add the path to the `instructions` array in `opencode.jsonc`. | The tool does not expand `@file` in `AGENTS.md` or `CLAUDE.md`. Use an absolute path. The docs do not confirm `~` in `instructions`. |
| Codex | None. | The Codex docs describe no include syntax. Codex does not get the personal file. |
| DSH | None. | DSH does not expand `@path`. The script `.claude/tools/gen_dsh.py` writes `~/.dsh/AGENTS.md` with the imported text inside. |

Sources: the memory docs of Claude Code, the rules page of OpenCode, and the `AGENTS.md` guide of Codex. The author read them on 2026-10-02. The author did not verify one point by running the tools. The point is whether the OpenCode permission `external_directory` affects an `instructions` path outside the project.

## Guard against personal text

The script `.claude/tools/persona-guard.py` refuses a push that contains the personal text. The hook `.claude/tools/githooks/pre-push` runs it. The guard needs the files `~/.claude/persona.md` and `~/.claude/persona-markers.txt`. Both files are in `.gitignore`.
