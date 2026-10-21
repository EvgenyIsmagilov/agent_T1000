# DeepSeek Harness (DSH)

DSH-specific pieces of the same agent setup as `../.claude` and `../.codex`.
Tested with DSH 0.2.x only.

## What is here

- `AGENTS.md` — the global instructions DSH reads from `$DSH_HOME/AGENTS.md`
  (`~/.dsh/AGENTS.md`). It is **generated**, not hand-written: `gen_dsh.py`
  inlines `../.claude/CLAUDE.md` and the text of each `@path` import, because
  DSH does not expand `@path`. This copy was built with the neutral
  `../.claude/persona.example.md` in place of the personal persona file.
- `skills-local/` — DSH versions of the skills whose text differs from the
  Claude Code version (`task-orchestration`, `code-tier-assessment`): models,
  delegation tool, no turn budget.

## Where the rest lives

DSH has no named subagents of its own. Each role is one
`@deepseek-ai/dsh-tool-subagent` row plus one agent preset in the profile
patch file. `../.claude/tools/gen_dsh.py` builds those from
`../.claude/agents/*.md`:

- `../.claude/tools/gen_dsh.py` — the generator (`--dry-run` shows the diff,
  `--write` applies it; close the DSH app before `--write`, the app rewrites
  the patch file). `../.claude/tools/test_gen_dsh.py` tests it.
- `../.claude/hooks/dsh-hooks.json` and `../.claude/hooks/dsh_adapter.py` —
  the hook configuration for `@deepseek-ai/dsh-hooks-claude-code` and the
  adapter that runs the Claude Code hook scripts under it. The command lines
  contain the placeholder `/Users/USER`: replace it with your home directory
  before you use the file.

## What is not here, on purpose

- `profiles/*/cordis.patch.yml`: the app owns and rewrites it, and it holds
  provider routes and local endpoints. Generate it with `gen_dsh.py`.
- `.credentials.yaml`, `.env`, sessions, and storages.
