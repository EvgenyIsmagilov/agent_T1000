# OpenCode

OpenCode-specific pieces of the same agent setup as `../claude` and `../codex`.

## What's here

- `agent/` — subagent definitions (`~/.config/opencode/agent/*.md`). OpenCode's
  `mode: subagent` frontmatter, invoked through the `task` tool.
- `skills/` — **overrides only**. OpenCode reads skills directly from
  `~/.claude/skills/*/SKILL.md` (a Claude-compatible discovery path — see
  [OpenCode's skills docs](https://opencode.ai/docs/skills/)), so most of
  `../claude/skills` needs no copy here at all. The two files under this
  `skills/` directory exist because their *content* differs from the Claude
  Code version — model names, the delegation tool, turn-budget mechanics —
  and a local `~/.config/opencode/skills/<name>/SKILL.md` shadows the shared
  Claude one for OpenCode sessions.
- `plugins/claude-hooks.js` — OpenCode's plugin-hook equivalent of the six
  Claude Code hooks under `../claude/hooks`: file-read guardrail, the
  orchestration gate, subagent-run logging, config-protection,
  docs-drift-reminder, and mcp-health-check, all in one file (OpenCode plugins
  are `tool.execute.before`/`tool.execute.after` handlers, not separate
  per-event scripts). Drop it into `~/.config/opencode/plugins/` — files there
  load automatically at startup, no config entry needed.
- `tools/` — read-only reporting scripts (`agent-stats.py`, `context-budget.py`),
  mirroring `../claude/tools`.

## What's *not* here, on purpose

`opencode.jsonc` (provider wiring, model routing, permissions) is not copied —
it holds this machine's local endpoints and provider choices, which don't
transfer as-is. The pieces that matter for orchestration are these two
`opencode.jsonc` blocks; adapt the model names to your own provider setup:

```jsonc
{
  "permission": {
    // Curated skill visibility — OpenCode discovers every skill under
    // ~/.claude/skills automatically; this narrows it down.
    "skill": {
      "*": "deny",
      "airflow": "allow",
      "code-style": "allow",
      "code-tier-assessment": "allow",
      "docker": "allow",
      "git": "allow",
      "grilling": "allow",
      "infra": "allow",
      "python": "allow",
      "seo": "allow",
      "stop-slop": "allow",
      "task-orchestration": "allow"
    }
  },
  "command": {
    "agent-stats": { "template": "Запусти `python3 ~/.config/opencode/tools/agent-stats.py $ARGUMENTS` и покажи вывод как есть. ..." },
    "context-budget": { "template": "Запусти `python3 ~/.config/opencode/tools/context-budget.py $ARGUMENTS` и покажи вывод как есть. ..." }
  }
}
```

`plugins/claude-hooks.js` reads `OPENCODE_TRINO_GUARDRAIL_PROJECT_ROOT` from
the environment (same convention as `../claude/hooks/trino-guardrail.py`'s
`TRINO_GUARDRAIL_PROJECT_ROOT`) — set it to your own project checkout, or
leave it unset to deny Trino MCP calls everywhere.

## Known gaps

`skills/task-orchestration/SKILL.md`'s own **OpenCode-specific notes** section
lists what's unverified in this setup: whether the `task` tool accepts a
per-call model override, whether a finished subagent session can be resumed,
and whether the local `skills/` override actually takes precedence over the
shared `~/.claude/skills` copy when names collide (OpenCode's docs describe
the scan order but not collision handling explicitly).
