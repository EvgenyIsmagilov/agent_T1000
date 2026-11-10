# OpenCode

OpenCode-specific pieces of the same agent setup as `../../.claude` and `../../.codex`.

## What's here

- `agent/` — subagent definitions (`~/.config/opencode/agent/*.md`). OpenCode's
  `mode: subagent` frontmatter, invoked through the `task` tool.
- `skills/` — **overrides only**. OpenCode reads skills directly from
  `~/.claude/skills/*/SKILL.md` (a Claude-compatible discovery path — see
  [OpenCode's skills docs](https://opencode.ai/docs/skills/)), so most of
  `../../.claude/skills` needs no copy here at all. The two files under this
  `skills/` directory exist because their *content* differs from the Claude
  Code version — model names, the delegation tool, turn-budget mechanics —
  and a local `~/.config/opencode/skills/<name>/SKILL.md` shadows the shared
  Claude one for OpenCode sessions.
- `plugins/claude-hooks.js` — OpenCode's plugin-hook equivalent of the six
  Claude Code hooks under `../../.claude/hooks`: file-read guardrail, the
  orchestration gate, subagent-run logging, config-protection,
  docs-drift-reminder, and mcp-health-check, all in one file (OpenCode plugins
  are `tool.execute.before`/`tool.execute.after` handlers, not separate
  per-event scripts). Drop it into `~/.config/opencode/plugins/` — files there
  load automatically at startup, no config entry needed.
- `tools/` — read-only reporting scripts (`agent-stats.py`, `context-budget.py`),
  mirroring `../../.claude/tools`, and `pr-review.py` with its tests (see
  [PR review](#pr-review) below). `pr-review.py` is not read-only: with `--post` it
  writes comments and reviews to GitHub, and with `--merge` it can merge the PR.
- `review/` — the review instructions (`pr-review-instructions.md`) and the system
  prompt of the `pr-reviewer` agent (`pr-reviewer.md`) that `pr-review.py` uses.

## What's *not* here, on purpose

`opencode.jsonc` (provider wiring, model routing, permissions) is not copied —
it holds provider wiring and model choices, which differ between setups and don't
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
      "appsec-review": "allow",
      "code-review": "allow",
      "code-style": "allow",
      "code-tier-assessment": "allow",
      "debugging": "allow",
      "docker": "allow",
      "git": "allow",
      "grilling": "allow",
      "infra": "allow",
      "python": "allow",
      "seo": "allow",
      "sql": "allow",
      "sql-review": "allow",
      "stop-slop": "allow",
      "task-orchestration": "allow",
      "trino-execution": "allow"
    }
  },
  "command": {
    "agent-stats": { "template": "Запусти `python3 ~/.config/opencode/tools/agent-stats.py $ARGUMENTS` и покажи вывод как есть. ..." },
    "context-budget": { "template": "Запусти `python3 ~/.config/opencode/tools/context-budget.py $ARGUMENTS` и покажи вывод как есть. ..." }
  }
}
```

`plugins/claude-hooks.js` reads `OPENCODE_TRINO_GUARDRAIL_PROJECT_ROOT` from
the environment (same convention as `../../.claude/hooks/trino-guardrail.py`'s
`TRINO_GUARDRAIL_PROJECT_ROOT`) — set it to your own project checkout, or
leave it unset to deny Trino MCP calls everywhere.

## PR review

`tools/pr-review.py` reviews a GitHub pull request with a model run through
`opencode` and, on request, posts the result. Settings come from the environment:

- `PR_REVIEW_BOT_LOGIN` (required) — the `gh` account the script writes as. The
  script stops with exit code 2 if the active `gh` identity differs.
- `OPENROUTER_API_KEY` (required) — passed to the model process.
- `PR_REVIEW_PERSONA_MARKERS` (optional) — path to a private file with one regular
  expression per line. A model answer that matches one is rejected. If the variable is
  unset or the file is unreadable, the check is skipped and a note goes to stderr.
  Keep that file out of the repository.

The script builds the run config from your `opencode.jsonc` (default
`~/.config/opencode/opencode.jsonc`), which must define a `pr-reviewer` agent. Add a
block like this one to your own file; the `prompt` path must be absolute:

```jsonc
"pr-reviewer": {
  "mode": "primary",
  "model": "openrouter/<your-model>",
  "steps": 50, // keep in sync with the step count named in review/pr-reviewer.md
  "prompt": "{file:/absolute/path/to/review/pr-reviewer.md}",
  "permission": {
    "*": "deny",
    "read": "allow",
    "grep": "allow",
    "glob": "allow",
    "external_directory": {"*": "deny"}
  }
}
```

Without `--post` the script only prints the verdict. Tests: `python3 tools/test_pr_review.py`
(seven tests that read the real `opencode.jsonc` are skipped when it is absent).

## Known gaps

`skills/task-orchestration/SKILL.md`'s own **OpenCode-specific notes** section
lists what's unverified in this setup: whether the `task` tool accepts a
per-call model override, whether a finished subagent session can be resumed,
and whether the local `skills/` override actually takes precedence over the
shared `~/.claude/skills` copy when names collide (OpenCode's docs describe
the scan order but not collision handling explicitly).
