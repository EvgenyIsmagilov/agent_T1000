---
name: mcp-servers
description: managing mcp servers/connectors in claude code while keeping the context window lean. use when adding, removing, or auditing mcp servers, editing .mcp.json or choosing a scope, debugging context bloat from too many tools, or fixing mcp auth (oauth, headless).
---

# MCP servers

Apply when adding, auditing, or troubleshooting MCP servers in Claude Code. Each server ships tool schemas; treat active tools as a context budget, and lean on tool deferral to keep that budget cheap.

## Tool budget and deferral

- On Opus/Sonnet with default settings, only tool *names* load at startup (~120 tokens for the whole list); full schemas stay deferred and load on demand via ToolSearch. Deferral is the free win — keep `ENABLE_TOOL_SEARCH` at its default (`auto`, which pre-loads schemas only when they fit in ~10% of the window).
- Deferral is unavailable on Haiku, on custom `ANTHROPIC_BASE_URL`, and on Google Cloud Agent Platform / Microsoft Foundry, or when `ENABLE_TOOL_SEARCH=false`. There every enabled schema loads upfront, and a dozen fat servers can eat a real slice of the window before compaction. In those setups, cut the number of active servers hard.
- Add a server only when a workflow needs it. There is no per-session "configured but disabled" toggle — the lean states are: not in config, or removed. (`deniedMcpServers` / `disableClaudeAiConnectors` in managed settings are org policy, not a personal on/off switch.)

## Scopes

Configure at the narrowest scope that fits. Precedence is Local > Project > User — the highest wins on a name clash.

- **local** (default): `~/.claude.json` under the current project's path — private, this project only.
- **project**: `.mcp.json` at the repo root — committed to git, shared with the team. Use only for servers the whole repo genuinely needs.
- **user**: `~/.claude.json` under `mcpServers` — private, applies to every project.

Manage from the shell with `claude mcp add|remove|list|get`; inspect status and reconnect in-session with `/mcp`. A local entry silently overrides a project one of the same name — run `claude mcp get <name>` when a server behaves unexpectedly.

## Secrets

- Keep literal tokens out of a committed `.mcp.json` — reference an environment variable, or put the authenticated server at local scope (which is not committed) instead.
- A static token passed via `--header` is the way to authenticate in headless runs.

## Auth

- OAuth servers authorize interactively: `/mcp` -> pick the server -> browser -> token stored in the system keychain; or `claude mcp login <name>` from the shell (`--no-browser` prints a URL to open on your local machine for SSH/headless).
- A headless/non-interactive session cannot run the OAuth flow. Pre-authorize once from an interactive session, or use a static-token server. With deferral on, an unauthorized server is reported as needs-auth rather than failing silently.

## Adding a server — checklist

1. Confirm a workflow actually needs it; weigh it against the tool budget, especially where deferral is unavailable.
2. Add at the narrowest scope. Commit to `.mcp.json` only when the whole repo needs it, and never with a literal token.
3. If it uses OAuth, authorize it interactively before any headless use.
4. Verify with `/mcp` (Connected) and confirm its tools resolve.
