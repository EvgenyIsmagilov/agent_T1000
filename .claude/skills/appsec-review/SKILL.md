---
name: appsec-review
description: "Security review of a code or configuration change: access control, injection (including SQL), secrets, personal-data exposure, dependencies, CI and release, LLM/agent/MCP code, and container or server configuration. Use as part of a code review whenever the diff touches authentication, authorization, secrets, untrusted input, data exposure, dependencies, CI workflows, prompt assembly or model tool calls, Dockerfiles, compose files, reverse proxies, SSH, firewall, or systemd units. Read-only: proves risks by reading and safe local checks, never by attacking a real system."
---

# Application security review

This skill adds a security lens to a review; it does not replace `code-review`. Posture, finding standards, severity scale, approval gate, and report structure come from `code-review` (or `sql-review` for an SQL-only target), and security findings go into that same `### Findings` list with the same fields. What this skill adds is where to look and what counts as evidence.

## When it applies

Load it when the diff changes any of: login, sessions, tokens, or permission checks; code that reads external input (HTTP, CLI args, files, queue messages, webhooks, LLM output); code that builds SQL, shell commands, paths, URLs, templates, or deserializes data; secrets, credentials, or their loading; logging of user or production data; dependency manifests and lockfiles; Dockerfiles, compose files, CI configs; nginx/caddy, SSH, firewall, systemd, cron; prompt assembly, model tool calls, agent memory, MCP servers or clients, and the hooks or permission rules that gate an agent; caches, exports, retention, or deletion of access-controlled data. A diff that touches none of these does not need it.

## Map the surface first

Before checking anything, write down for yourself:

1. **Entry points** the change adds or alters — every place untrusted data enters.
2. **Trust boundaries** it crosses — user → service, service → database, container → host, internet → reverse proxy.
3. **Assets** at stake — credentials, personal data, production data, money, host access.
4. **Who can reach it** — anonymous, authenticated, internal only, operator only.

A risk only counts if untrusted data or an untrusted actor can actually reach it along a path you traced. "This function would be unsafe if called with attacker input" is not a finding until you show a caller that passes such input.

## Domain references

The checks below cover every security diff. When the surface map hits one of these domains, also read the matching file in `references/` — only that file, only the classes the diff reaches:

| The diff touches | Read |
|---|---|
| Prompt assembly, RAG, agent memory, model tool calls, MCP, sub-agent delegation, agent hooks or permission rules | `references/ai-and-llm.md` |
| Dependency resolution, lockfiles, CI workflows, build inputs, release, signing, updaters, plugins | `references/supply-chain-and-release.md` |
| IAM, infrastructure as code, containers, Kubernetes, serverless, ingress, metadata endpoints, runtime config | `references/cloud-and-deployment.md` |
| Sessions, cookies, CSRF, JWT, OAuth/OIDC, SAML, MFA, password recovery, API keys, mTLS, HTTP parsing or caching | `references/web-protocol-and-auth.md` |
| Multi-tenant or ACL'd stores, derived copies (cache, search, analytics), object links, export, backup, migration, deletion, retention | `references/data-isolation-and-lifecycle.md` |
| Untrusted work that consumes shared CPU, memory, disk, connections, workers, queues, quotas, or paid spend (heavy Trino queries included) | `references/resource-exhaustion-and-availability.md` |

The references come from a whole-codebase audit workflow; use their attack classes and validation rules, not their process.

## Checks

### Access control
- Every new or changed endpoint, command, or task checks authentication and authorization on the server side, not only in the UI or the caller.
- Object-level checks: can user A read or change user B's record by changing an id (IDOR)?
- Defaults deny: a new role, flag, or route does not silently get broad access; a failed check fails closed.
- Privileged paths (admin, migrations, backfills, `ic.temp`-style scratch areas) are not reachable from ordinary flows.
- Alternate paths: no second route to the same state change checks a weaker permission; a request-body field cannot override what the permission check restricted; batch, bulk, import, and export paths check every item, not just the first or the container.

### Injection
- SQL, including Trino: values go through bind parameters or a safe query builder; identifiers (table, column, schema) built from input are checked against an allowlist. f-strings, `%`, `.format`, or Jinja rendering of untrusted values into SQL is a finding. Airflow templating that inlines `params`/`dag_run.conf` into SQL counts as untrusted input.
- Shell: no `shell=True` or `os.system` with untrusted parts; arguments passed as a list.
- Paths: user-controlled paths are normalized and confined to the intended directory (traversal via `..`, absolute paths, symlinks).
- Deserialization: no `pickle`, `yaml.load` without `SafeLoader`, or `eval`/`exec` on untrusted data.
- Server-side requests: URLs from input cannot reach internal hosts, cloud metadata endpoints, or `file://` (SSRF).
- Templates and output: user data is escaped for its context (HTML, headers, logs — log injection via newlines).
- LLM output fed into any of the above is untrusted input too.
- Indirect paths: data stored safely and later read back by other code into a sink; injection through field names, keys, headers, and metadata, not only values; injection into secondary systems (logs, caches, search indexes, analytics).

### Secrets
- No credentials, tokens, keys, or connection strings in code, configs, tests, fixtures, notebooks, Dockerfiles, or compose files; they come from environment variables or a secret store.
- Secrets are not printed, logged, put in exception messages, URLs, or query strings, and are not baked into image layers (`ARG`/`ENV` with a secret, `COPY` of `.env`).
- A secret that was committed is compromised even if a later commit removes it — the fix direction is rotation, not deletion.

### Data exposure
- Responses, logs, metrics, and error messages do not carry personal data, raw production values, or stack traces to untrusted callers.
- Queries and exports select only the columns they need; personal data leaving the warehouse (to a notebook, chat, file, third party) is justified by the task.
- New storage of personal data has a stated retention and access scope.

### Cryptography and sessions
- No home-made crypto; standard libraries for hashing passwords (bcrypt/scrypt/argon2), tokens from a CSPRNG (`secrets`, not `random`), TLS verification left on (`verify=False` is a finding).
- Tokens and sessions expire, and can be revoked where the design needs it.

### Dependencies
- New or upgraded packages: pinned versions, from the expected registry, name spelled right (typosquatting), actually needed — an existing dependency or the standard library may already cover it.
- Do not claim a known vulnerability (CVE) without a source; if you cannot check, say that the dependency was not audited.

### Containers and servers
- Containers: not running as root without need; no `--privileged`, host network, or Docker socket mount without justification; only needed ports published, bound to `127.0.0.1` when only the host needs them; base images pinned.
- Reverse proxy: TLS on, no open proxy, sensible body-size and timeout limits, admin paths not public.
- SSH and firewall: key-only auth, no root login, default-deny firewall with explicit allows.
- systemd/cron: least-privilege user, no world-writable scripts or env files, secrets not in unit files.

## Evidence rules

- Prove by tracing and by safe local execution only: call a sanitizer or parser with a crafted literal (`python -c`), run an existing test. Never send a payload to a running service, a shared database, or anything over the network, and never try a credential.
- Name the exact entry point and the path to the sink (`file:line` → `file:line`). For every candidate state the lower-trust actor, the input or action they control, the control that should stop them, the boundary crossed, the affected principal or resource, and the concrete result.
- Every candidate ends in one of two states:
  - `confirmed` — the full path is traced in source (and, where useful, shown by a safe local check). It goes into `### Findings` with a severity.
  - `needs_validation` — the path is real in source, but one decisive fact lives outside the repository (proxy or provider behavior, deployed config, branch protection, identity policy). Do not assume that fact either way. It gets no severity; put it in `### Missing verification` with the exact missing fact and a safe check the owner can run. If it could hide a `CRITICAL` or `HIGH` result, it is an unresolved question that blocks `APPROVE` under `code-review`.
- Severity follows demonstrated impact and reach, never what the bug class "usually" means:
  - `CRITICAL` — an unauthenticated actor gets code execution, full data-store access, or takeover of arbitrary accounts.
  - `HIGH` — an explicit control is fully defeated with real consequences: authentication bypass, cross-tenant read or write, stored script execution against other users, authenticated code execution, unauthenticated stop of a shared service.
  - `MEDIUM` — a real boundary violation with a narrow blast radius, uncommon preconditions, or effects confined to few resources.
  - `LOW` — disclosure of non-secret internals, or an effect that costs the attacker a lot for little gain.
  - High or medium: does the result fully defeat the control for an action with real consequences, or only weaken it? If you cannot name the concrete damage, the severity is lower than it feels.
- Fix direction: name the invariant the code must enforce and the narrowest change that enforces it at the last trusted decision point, plus the regression test that would catch it — not generic hardening advice.
- Never paste a secret, token, or personal data into the report, even partially — name the file and line.

## Not findings

- A checklist deviation or missing best practice with no reachable boundary violation.
- A missing second layer when the first layer already stops the attack — at most a `LOW` hardening note, labeled as such.
- Guessed behavior of a proxy, provider, browser, identity system, or deployment that is not in the repository — that is `needs_validation`, not a finding and not a pass.
- Self-impact, or a principal using authority it legitimately has (a model executing what the user asked for is not a privilege escalation).
- Prompt injection by itself, without a code-level failure: content reaching another principal's context, authority the requester lacks, data they cannot read, or a sink they cannot reach.
- A known-vulnerable or unpinned dependency with no shown path from who influences it to what it executes or ships.
- A parser crash or runtime effect reported as stronger than what was observed.

## Reporting

Add `**Security surface:**` to `### Review scope`: the entry points and trust boundaries you checked and the `references/` files you read, in one or two lines. Security findings go into `### Findings`; security checks you performed go into `### Challenges performed`; what you could not verify (for example, runtime config, a dependency audit, a proxy config outside the repo) goes into `### Missing verification`. No separate security verdict.
