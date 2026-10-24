# Frontmatter — Subagent Definitions

Field reference for a subagent `.md`. Disclosed reference for [`writing-great-agents`](SKILL.md); consult a row when that field is in question.

Source: Claude Code docs, `code.claude.com/docs/en/sub-agents`, checked 2026-09-20. Re-check before relying on a version-gated row.

## Required

| Field | Rules |
|---|---|
| `name` | Lowercase letters and hyphens. No `:` — that is reserved for plugin scoping (`my-plugin:reviewer`). The filename need not match, but matching it keeps the folder greppable. |
| `description` | When the parent should delegate here. Loaded every turn; all descriptions together passing 15,000 tokens trips a startup warning. |

## Optional

| Field | Values | Default when omitted |
|---|---|---|
| `tools` | Tool names or patterns — `Read, Grep, Glob, Bash`; MCP as `mcp__<server>` or `mcp__<server>__*` | Inherits every tool available to subagents, `Agent` included |
| `disallowedTools` | Tool names or patterns | None. Applied **before** `tools`; a tool in both is removed |
| `model` | `sonnet`, `opus`, `haiku`, `fable`, a full id (`claude-opus-5`), or `inherit` | Resolution order below |
| `permissionMode` | `default`, `acceptEdits`, `auto`, `dontAsk`, `bypassPermissions`, `plan`, `manual` (alias of `default`) | Inherits the main conversation's mode |
| `maxTurns` | Positive integer | Unlimited. On hit, the run is marked partial |
| `effort` | `low`, `medium`, `high`, `xhigh`, `max` | Inherits from the session |
| `skills` | List of skill names | None. Preloaded at startup, before the first turn |
| `mcpServers` | Server names or inline definitions | None |
| `hooks` | Hook config object (`PreToolUse`, `PostToolUse`, `Stop`) | None |
| `memory` | `user`, `project`, `local` | None — no cross-session memory |
| `background` | `true` / `false` | `false`. Background runs get a reduced tool set |
| `omitClaudeMd` | `true` / `false` | `false`. `true` skips user/project/local `CLAUDE.md` (v2.1.271+) |
| `isolation` | `worktree` | None — runs in the main checkout |
| `color` | `red`, `blue`, `green`, `yellow`, `purple`, `orange`, `pink`, `cyan` | None. Display colour in the task list |
| `initialPrompt` | Text | None. Auto-submitted as turn one when the agent runs as a main session |
| `experimental` | Map with `cacheTtl`: `5m` or `1h` | None (v2.1.248+) |

## Model resolution order

With `model` omitted or set to `inherit`:

1. per-invocation `model` passed by the caller;
2. the `model` in this frontmatter;
3. `CLAUDE_CODE_SUBAGENT_MODEL` env var;
4. the main conversation's model.

Forcing every subagent onto one model (v2.1.257+): set `CLAUDE_CODE_SUBAGENT_MODEL` plus `CLAUDE_CODE_SUBAGENT_MODEL_FORCE: "1"` in `env`.

## File locations and precedence

Highest wins:

1. managed settings (organization-wide);
2. `--agents` CLI flag (this session);
3. `.claude/agents/` (project, version-controlled, discovered walking up from the working directory);
4. `~/.claude/agents/` (all projects — where personal agents live);
5. a plugin's `agents/` directory, addressed as `plugin:name`.

Two files sharing a `name` in one directory resolve in filesystem read order — unpredictable. Across nested project directories, the definition closest to the working directory wins. `/doctor` reports duplicates.
