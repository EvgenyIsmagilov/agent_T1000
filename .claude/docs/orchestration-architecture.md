# Orchestration architecture

What the pieces are, why they're shaped this way, and what must stay in sync
when one of them changes. `task-orchestration`/`code-tier-assessment` are
the operational procedure ("what to do now"); this doc is the concept map
("why it's built this way"). Keep instructions in the skills, keep rationale
and cross-file invariants here.

## Core idea

The main agent is the task owner: it keeps requirements, decisions, and the
completion call in its own context. Subagents are isolated executors —
narrow briefs in, a compact fixed-format report out. Two independent axes
decide which subagent runs:

- **Role** — what kind of work (write code, review code, run tests,
  research, diagnose a bug). Fixed per agent file.
- **Tier** — for `code-writer` only, how strong a model/effort to spend.
  Fixed per agent file too, because effort can't be overridden on an
  `Agent` call — only `model` can.

## Roles

| Role | Job | Model / effort |
|---|---|---|
| `code-writer-t1/t2/t3` | implement one slice | see Tiers below |
| `reviewer` | adversarial review of a diff; loads `code-review`, plus `sql-review`/`sql` for SQL and `appsec-review` for security surfaces | opus / xhigh |
| `planner` | read-only sliced plan, risks, acceptance criteria | sonnet / high |
| `test-runner` | run existing checks, report failures | haiku / low |
| `bug-investigator` | reproduce + root-cause a defect | sonnet / high |
| `docs-researcher` | version-sensitive research | sonnet / high |
| `web-researcher` | quick factual lookup | haiku / low |
| `finding-verifier` | refute one review finding | sonnet / medium |

`finding-verifier` checks one claim someone else made, usually a single
`reviewer` finding you doubt before paying for the fix. It was built for
the `verify-review-findings` workflow, which needed a read-only agent that
answers with a schema: `general-purpose` would have done the job but holds
`Edit` and `Write`, and the local read-only roles carry Markdown contracts that
a schema erases. That workflow is parked (see the header of
`~/.claude/workflows/verify-review-findings.js`), so the agent is delegated by
hand and falls back to a three-line text answer when no schema is supplied.

It does have an entry in `log-subagent.py`'s `ROLE_CONTRACTS`, but a
`style: "line"` one, not the `### heading` shape the other roles use: the
contract just checks that the reply opens with `Verdict: <status>` and nothing
before it, plus the `Checked`/`Result` lines with content. Under a schema the
reply is JSON, not those three lines, so the contract still runs against it and
fails: `contract_status` comes out `invalid` and, since no `Verdict:` line can
be extracted, `verdict_status` comes out `lost` — never `exempt`, which is
reserved for agent types with no entry in `ROLE_CONTRACTS` at all.

`bug-investigator` runs at two points: up front, when the task is a reported
defect that must be reproduced before a fix is planned, and later, when a check
fails for an unknown reason. One run returns both the reproduction and the root
cause, so the two points never double-charge for the same defect.
`reviewer` already names the cause (evidence + fix direction), so its
findings go straight to the executor, never through `bug-investigator`.

`reviewer` may execute a counterexample rather than only predicting its
outcome, and `Confidence: high` now requires either an executed case or a defect
visible on the changed line. The ban on `airflow dags test`, `airflow tasks
test`, backfills, and non-`SELECT` queries is written by command name on
purpose, not left to a risk judgment: "cheap and side-effect-free" is exactly
the phrase a reviewer would talk itself past, and there is no safe Airflow
target configured to be wrong against.

Decided 2026-09-24: `reviewer`'s read-only rule is enforced, not only
written. It has no `Edit`/`Write`, but its `Bash` could `git stash` or
`git checkout -- .` the uncommitted diff it was judging — often the only copy.
`hooks/reviewer-bash-guard.py` is registered in the agent's own frontmatter
(so it runs only while `reviewer` is active) and allows a read-only command
list, fail-closed: an unparseable command is denied, because a rephrase costs
the reviewer one turn and a lost diff costs the task. It cannot see what
`python -c` does; the prose rule still covers that. `sed`, `awk`, `sort`,
`uniq`, `find`, and `file` are deliberately off the list rather than guarded:
two review rounds kept finding write spellings a flag denylist missed
(`sort -uo`, `sed -n '1wout'`, gawk `-iinplace`), and `python -c`, allowed
anyway, does everything they do — guarding them bought code, not safety.
Heredocs and here-strings are denied for the same reason (decided
2026-09-26): three review rounds showed a shlex-based skipper cannot tell
where the shell ends a body (an apostrophe in a comment, a quoted `'-EOF'`
delimiter), and a multi-line `python3 -c '...'` does the same job. Brace
characters are denied in redirect targets, since brace expansion can move a
literal `$TMPDIR/{..,x}/…` path outside `$TMPDIR`. For the same reason a
redirect target accepts `$TMPDIR`/`${TMPDIR}` only as a whole prefix and no
other `$` or glob character.

Known residual (verified in zsh 2026-09-26, not fixed): the hook tokenizes
with shlex, which does not read quoting the way zsh does. An apostrophe inside
a `#` comment opens a shlex quote, so a command line between two such comments
is never checked; `$'\''` and backslash-newline continuations desync the same
way. Accepted as medium-low under the careless-agent threat model — the common
accidents are single plain commands, and those are caught. If it is ever
fixed, fix it the way the rest of this hook was fixed: deny unquoted `#` at a
word start, `$'`, and backslash-newline, rather than teaching the parser zsh.

Decided 2026-09-26: the primary protection of work under review is recovery,
not prevention. After every implementer run the orchestrator snapshots the
working tree into `refs/snapshots/<slice_id>/<n>` (command and rules in
`task-orchestration` → **Snapshots**): a commit object built through a
temporary index, so tree, index, and branch stay untouched, untracked files
are in, ignored ones out. Any destructive command — through the hook's
residual gap or not — becomes one `git checkout <ref> -- .` away from undone,
and re-review gets a real delta between snapshots. A separate worktree was
considered and rejected: the whole pipeline would have to run there, a subagent
worktree branches from the default branch rather than the current state, and
the result has to be merged back — all to keep iterations, which a ref does in
one command. The user authorized snapshots as a standing exception to "commit
only when asked". The hook stays as a second net at v5; the final round against
the shlex/zsh residual was dropped as not worth its cost once snapshots exist.
Not covered: repositories that are not git (the harness in `~/.claude` itself —
copies into the scratchpad instead), and a deliberate `rm -rf .git`, which the
hook denies. `disallowedTools` with a
`Bash(...)` specifier was not an option — it removes the whole tool. Mirrors:
Codex already runs `reviewer` with `sandbox_mode = "read-only"`; OpenCode uses
`permission.bash` with `"*": deny`, which does not see output redirections.
`permissionMode: default` was dropped in the same change: it forced manual
prompts even under an auto-mode parent, and the hook is the boundary now.
Re-review got its own rules in `code-review` (previous findings first, then the
delta) so a fix round does not pay for a full opus/xhigh review again.

`opus` never writes code — it is reserved for the two reviewer roles. That's
a deliberate boundary, not an oversight: review benefits from the strongest
available judgment on someone else's finished work; writing benefits more
from tiering, because most slices are not hard.

## Tiers (code-writer)

Three separate agent files, not one file with a `model` override, because
T1→T3 needs effort to change too:

| Tier | Agent | Model | Effort | Target share |
|---|---|---|---|---|
| T1 | `code-writer-t1` | haiku | max | ~30% |
| T2 | `code-writer-t2` | sonnet | medium | ~50%, default |
| T3 | `code-writer-t3` | sonnet | max | ~20% |

Classification is a separate skill, `code-tier-assessment`, not folded into
`task-orchestration` — so the closed-list criteria for T1/T3 can be revised
without touching the escalation/retry mechanics, and vice versa.

Target shares are a calibration goal, checked after the fact with
`first-pass.py`, never a quota enforced on one task.

T2 and T3 work test-first (failing test → smallest passing code, one piece of
behavior at a time); T1 does not, because a red-green cycle on a one-line fix
doubles the cost of the cheapest tier for nothing. Two deliberate choices sit
behind the shape of this:

- **Rules inlined in the agent files, not a named skill.** Test-first is meant
  to be always-on for those tiers; a skill only loads when the brief names it,
  and a brief that forgets it would silently fall back to test-after. The
  `tdd` skill these rules came from was deleted on 2026-09-20 once they were
  inlined, so the agent files are the only copy — there is no second source to
  drift from. Upstream, if the longer prose or its good/bad test examples are
  ever wanted again: `mattpocock/skills`, `skills/engineering/tdd/`.
- **Coverage is scoped, not measured.** The brief's `test_scope` names the
  surface and the failure modes; there is no percentage target. An executor
  cannot measure coverage in most repos and would report a guessed number, and
  where it can measure, a percentage target is met most cheaply by tests that
  execute lines without asserting anything. If a real number is ever wanted, it
  belongs to `test-runner` with an actual tool, never to the writer's own
  account of its work.

## How a slice is measured

1. The orchestrator writes a brief naming `slice_id` and `tier`.
2. `code-writer-t*` runs, returns a fixed-format report (`### Result` /
   `Changes` / `Verification` / `Follow-ups`).
3. The `SubagentStop` hook (`log-subagent.py`) fires right after, reads the
   agent's own transcript (not the report text) for turns/tokens/model, reads
   the brief for `slice_id`/`tier`, validates the report against that role's
   contract, and appends one JSONL line to `~/.claude/logs/subagent-runs.jsonl`.
4. `agent-stats.py` aggregates that log (cost, report validity, budget
   warnings). `first-pass.py` joins rows by `slice_id` to ask whether a slice
   closed without a retry, split by tier.

Nothing here is inferred from memory or from what an agent claimed about
itself — every number comes from the transcript or the hook's own
validation of the report structure.

The brief also carries `worktree`, `branch` and `merge_gate`. They are inert
today: slices run sequentially in the main checkout, and all three
`code-writer-t*` files forbid committing, so nothing can write to a named
branch. They exist so that the record of where a slice ran is written down
before parallel slices need it, and because `merge_gate` names the one case the
failure classes would otherwise get wrong — a merge conflict between two slices
is `slicing`, never `executor`. `log-subagent.py` does not parse them:
`brief_fields.py`'s `BRIEF_FIELDS` extracts `slice_id`, `tier` and
`previous_failure_class` only, so adding brief fields costs the log nothing.

## Escalation

Each tier gets 2 attempts. A failure is classified before any retry:
`executor` (model wasn't capable — raises the tier, one step at a time,
T1→T2 or T2→T3), `spec` (bad brief — replan, tier unchanged), `env` (flake —
fix and retry, tier unchanged), `slicing` (slice too big — recut, tier
unchanged). Only `executor` ever moves the tier. Hard ceiling: 9 implementer
runs on one slice across all classes.

## Continuing an implementer instead of respawning

A finished subagent stays addressable through `SendMessage` with its context
intact, so a second round on the same slice has a choice: continue the agent
that did the work, or brief a fresh one. The rule in `task-orchestration`
decides by failure class — continue while the class says the agent's reasoning
is sound (a named reviewer finding, a named failing check, `env`), start fresh
when it says the reasoning is not (`spec`, `slicing`) or when the tier changes
(`executor`, where a different agent file makes continuing impossible anyway).

The cost argument runs both ways and does not settle it on its own. A continued
agent re-sends its whole history on every turn, so its turns are expensive and
few; a fresh agent has cheap turns but spends several of them re-reading. Which
wins depends on how long the first run was, and the fit behind those numbers
(`23.2k + 0.513k × turns`, R² 0.68 over 17 runs) is too loose to decide policy.
What does decide it: the brief already names `files_to_inspect` and step 11
forwards `file:line` verbatim, so the agent is applying a named fix at a named
place rather than searching — the frame it is "stuck in" barely matters there.

Two limits are recorded rather than solved:

- **Turn budget.** `maxTurns` is 30/80/110 by tier and logged writer runs span
  12 to 101 turns. Whether a continuation resets that budget is unverified, so
  the skill refuses to continue a run that used more than two thirds of its cap.
- **The tally goes partly blind.** It is unknown what `log-subagent.py` records
  for a continued agent: nothing, the cumulative transcript, or just the new
  turns. If it records nothing, `first-pass.py` counts a slice as closed on the
  first try when it was not, and the first-pass rate drifts upward. The attempt
  count and the 9-run ceiling are therefore kept by the orchestrator itself, not
  read back from the log. Resolve this by continuing one throwaway subagent and
  reading the log; until then treat first-pass numbers as an upper bound.

## Gates

- `require-orchestration.py` (PreToolUse on `Agent`): refuses to delegate
  until the `task-orchestration` skill has been loaded this session. It also
  refuses to delegate to `code-writer-t1/t2/t3` or `test-runner` unless the
  prompt carries a `slice_id:` line (reusing `BRIEF_RE` from
  `brief_fields.py`, shared with `log-subagent.py`) — 66 of 82 logged runs had
  none, leaving `first-pass.py`'s per-slice metrics empty. Ad-hoc runs can use
  `slice_id: adhoc`. Kill switch: `CLAUDE_SLICE_GATE=0` (separate from the
  doctrine gate's own `CLAUDE_ORCHESTRATION_GATE=0`). The OpenCode plugin
  mirrors both checks with `OPENCODE_ORCHESTRATION_GATE=0` /
  `OPENCODE_SLICE_GATE=0`.
- `check-file-size.py` (PreToolUse on `Read`): refuses a whole-file read
  above 350 lines, forcing large reads through `Explore` or `offset`/`limit`.
- `config-protection.py` (PreToolUse on `Edit`/`Write`): refuses to weaken a
  linter or type-checker config. It is what enforces the completion gate's
  "no validation was disabled or weakened merely to pass", which until now was
  prose nobody checked. A dedicated config (`ruff.toml`, `.flake8`,
  `.pre-commit-config.yaml`, ...) is blocked on modification and allowed on
  first creation — there is nothing to weaken in a project that has none. A
  mixed file (`pyproject.toml`, `setup.cfg`, `tox.ini`) has the edit applied in
  memory and only its `[tool.ruff]`-class sections compared before and after, so
  a dependency bump passes and a widened `ignore` list does not. That mixed-file
  comparison is the part ECC's `pre:config-protection` lacks: upstream skips
  `pyproject.toml` entirely, which in a Python repo is where the settings
  usually live. Kill switch: `CLAUDE_CONFIG_PROTECTION=0`.
- `docs-drift-reminder.py` (PostToolUse on `Edit`/`Write`): reminds, once per
  session per file, when an orchestration file changes — see **Change
  discipline** below. It cannot judge whether the change is significant;
  that's still a human/agent call.
- `mcp-health-check.py` (PostToolUseFailure on `mcp__.*`): not a gate — it
  cannot block, because the tool has already failed. It classifies the error
  (`auth`, `forbidden`, `rate_limit`, `unavailable`, `transport`), counts
  consecutive failures per server, appends a masked row to
  `~/.claude/logs/mcp-failures.jsonl`, and returns `additionalContext` so the
  running turn learns the server is down instead of retrying a dead one.
  Ported from ECC's `post:mcp-health-check`; its reconnect path was
  deliberately dropped — upstream runs an env-supplied shell string, and
  reauthorizing a server is the user's action, not a hook's. Kill switch:
  `CLAUDE_MCP_HEALTH=0`.

All hooks fail open: a bug in a guardrail must never be the reason a normal
edit or delegation breaks.

## Invariants — keep these in sync

These are cross-file couplings with no compiler to catch drift:

- `log-subagent.py`'s `ROLE_CONTRACTS` keys must match the `name:` in each
  agent file's frontmatter exactly, or that agent's reports silently validate
  as `exempt` instead of `valid`/`invalid` — format drift goes undetected.
- `brief_fields.py`'s `tier` regex (`BRIEF_FIELDS["tier"]`) must accept every
  tier name currently in use (`T[123]` for three tiers).
- `first-pass.py`'s `IMPLEMENTERS` tuple must list every `code-writer-t*`
  agent name, or slices from a missing one are silently excluded from the
  first-pass-rate calculation.
- Effort is fixed per agent file and cannot be set on an `Agent` call — any
  new tier or role needs its own file, never a call-time parameter.
- `opus` writes no code. If that ever changes, it's a deliberate, named
  exception, not a quiet default.

## Known open gaps (as of 2026-09-20)

Fixed same day: T1 now also caps the total diff at 200 lines across every
touched file, not just per file; the vague T3 "needs a lot of context"
criterion was replaced with a concrete file-count trigger (>15 files); three
one-line worked examples were added.

Open (2026-09-20): the work loop now calls `bug-investigator` up front to
reproduce a reported defect, but that agent is not in
`require-orchestration.py`'s `EXEMPT_AGENTS`. Inside the loop this is correct —
the skill is loaded by then. The friction is the case just outside it: a defect
that looks trivial enough to skip orchestration still has to be reproduced, and
that delegation is denied. Cost is one turn, and the gate log holds a single
deny ever (on `test-runner`), so this is recorded, not fixed. Revisit only if
`~/.claude/logs/orchestration-gate.jsonl` accumulates denies on
`bug-investigator` — exempting it would also weaken the gate at the in-loop
call site, which is the common one.

Decided 2026-09-23: `reviewer` joined `EXEMPT_AGENTS` (and the OpenCode
plugin's list). `/code-review` delegates to it alone, and loading the full
doctrine for one read-only review was pure context cost. The same weakening
the `bug-investigator` note above warns about applies — an in-loop review no
longer proves the skill was loaded — and was accepted because the in-loop
orchestrator has already loaded it to get that far.

## Change discipline

Adding, removing, or renaming a role or tier, or changing the brief/log
schema, touches several files at once: the agent file(s) themselves,
`task-orchestration` and/or `code-tier-assessment`, `log-subagent.py`
(`ROLE_CONTRACTS`, tier regex), `first-pass.py` (`IMPLEMENTERS`, `tier_of`),
and this doc. `docs-drift-reminder.py` flags when one of those files changed
so the question gets asked — it does not decide the answer.
