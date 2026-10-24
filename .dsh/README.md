# DeepSeek Harness (DSH)

This directory holds the DSH parts of the agent setup. The other parts are in `../.claude` and `../.codex`. The files were tested with DSH 0.2.x only.

## Files in this directory

- `AGENTS.md` is the global instruction file. DSH reads it from `$DSH_HOME/AGENTS.md`, which is `~/.dsh/AGENTS.md`. The script `gen_dsh.py` generates it. Do not edit it by hand. The script copies `../.claude/CLAUDE.md` into the file and adds the text of each `@path` import, because DSH does not expand `@path`. This copy uses the neutral file `../.claude/persona.example.md` in place of the personal persona file.
- `skills-local/` holds the DSH versions of two skills: `task-orchestration` and `code-tier-assessment`. Their text differs from the Claude Code version in three points: the models, the delegation tool, and the missing turn budget.

## Files in other directories

DSH has no named subagents. Each role needs one `@deepseek-ai/dsh-tool-subagent` row and one agent preset in the profile patch file. The script `gen_dsh.py` builds both from `../.claude/agents/*.md`.

- `../.claude/tools/gen_dsh.py` is the generator. Use `--dry-run` to see the changes. Use `--write` to apply them. Close the DSH app before you use `--write`, because the app rewrites the patch file.
- `../.claude/tools/test_gen_dsh.py` tests the generator.
- `../.claude/hooks/dsh-hooks.json` is the hook configuration for `@deepseek-ai/dsh-hooks-claude-code`.
- `../.claude/hooks/dsh_adapter.py` runs the Claude Code hook scripts under DSH.
- The command lines in `dsh-hooks.json` contain the placeholder `/Users/USER`. Replace it with your home directory before you use the file.

## What is not in this directory

- The files `profiles/*/cordis.patch.yml`. The app owns and rewrites them. They hold provider routes. Use `gen_dsh.py` to generate them.
- The file `.credentials.yaml`, the file `.env`, the sessions, and the storages.
