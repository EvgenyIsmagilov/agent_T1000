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
