---
name: task-orchestration
description: orchestrate non-trivial tasks with specialized subagents in DeepSeek Harness. use when work needs multiple steps, delegation to subagents, implementation plus verification, independent review, root-cause debugging, external research, or trino/sql optimization.
---

# Task orchestration (DeepSeek Harness)

Act as the task owner. Keep requirements, decisions, and the final completion decision in the main context. Delegate only focused work that benefits from isolation or specialization.

This is the DeepSeek Harness (DSH) version of the Claude Code doctrine. The roles are the same ten; how they are called, what they cost, and what is enforced for you differ — see **What DSH does and does not enforce** at the end.

## Decide whether to orchestrate

Only a `code-writer` tier edits source code or repository configuration — never you, however small the change; a trivial change goes to `code-writer-t1` with a short brief. Instruction, documentation, and harness configuration files (`AGENTS.md`, skills, agent definitions) are not code for this rule. Answer read-only questions, research, and document-only work directly or through a researcher.

Use the full workflow below when the task has multiple steps, uncertain behavior, meaningful risk, noisy validation, external research, or requires independent review.

## How subagents are called here

Each role is its own tool, named after the role: `reviewer`, `planner`, `finding-verifier`, `bug-investigator`, `test-runner`, `web-researcher`, `docs-researcher`, `code-writer-t1`, `code-writer-t2`, `code-writer-t3`. Each takes `description` (3–5 words) and `prompt` (the brief). Model, effort, and the allowed tool set are fixed per role in the profile; you cannot change them per call.

- There is no `Explore` role. For a broad read-only search, use the generic `subagent` tool with a read-only brief, or search yourself with `grep`/`glob` when you already know the vocabulary.
- `subagent_fork` copies your whole context into the child. Do not use it for delegation under this doctrine: the brief, not the conversation, is what a subagent gets.
- Continue a finished subagent with `send_message`; stop a runaway one with `interrupt_agent`.

## Delegate narrowly

Give every subagent only:

- the exact objective;
- relevant files, commands, versions, and constraints;
- the names of applicable skills to load (never their pasted content);
- required evidence;
- the expected compact output;
- the condition that ends the subtask.

Do not pass the full conversation, unrelated files, or raw historical logs. Ask for conclusions, evidence, and actionable findings.

Use subagents by role:

- `planner`: read-only implementation plan with slices, risks, and acceptance criteria, for a task whose approach is genuinely open — ahead of writing the brief below;
- `web-researcher`: narrow factual or documentation lookup;
- `docs-researcher`: ambiguous, version-sensitive, or high-impact research;
- `test-runner`: focused tests, linters, type checks, and build checks;
- `bug-investigator`: root-cause analysis after a non-obvious or repeated failure;
- `code-writer`: focused implementation of a single well-scoped feature slice, fix, or refactor; runs at three tiers (`code-writer-t1/t2/t3`), see **Executor tiers** and the `code-tier-assessment` skill;
- `reviewer`: the single adversarial review gate. It always loads `code-review`, adds `sql-review` and `sql` for meaningful Trino or analytical SQL, `appsec-review` for a security-sensitive surface, and `airflow`/`python` where the change needs them — one run, one verdict;
- `finding-verifier`: verify one specific claim (a review finding, a suspected diagnosis) before spending a `code-writer` run on it — read-only, refutation-first, never hunts for other defects.

Run subagents in parallel only when their work is independent and read-only. Never let parallel workers edit the same files.

## Budgets

Every delegation costs tokens and wall-clock, and every report lands back in your context. Spend deliberately.

| Role | Model (OpenRouter unless noted) | Effort |
|---|---|---|
| `reviewer` | `qwen/qwen3.8-max-0902` | xhigh |
| `planner`, `bug-investigator`, `docs-researcher` | `~google/gemini-pro-latest` | high |
| `finding-verifier` | `~z-ai/glm-latest` | low |
| `test-runner`, `web-researcher` | local `dspark/deepseek-v4-flash` | — |

Writers are in **Executor tiers**. The table mirrors `~/.claude/tools/gen_dsh.py`; if they disagree, the generator wins.

- At most 3 subagents in flight at once, and only when each answers a different question.
- No role has a turn limit here. A brief must name the condition that ends the subtask, or a run can spin until it exhausts the model's output budget.
- Prefer the local `dspark` roles (`test-runner`, `web-researcher`) for cheap, noisy work; they cost no OpenRouter spend.
- Never pay twice for the same evidence. A check `code-writer` ran and reported does not get rerun by `test-runner`, and a file a subagent summarized does not get re-read in the main context.

## Executor tiers

| Tier | Tool | Model | Effort |
|---|---|---|---|
| T1 | `code-writer-t1` | `~deepseek/deepseek-v4-flash-latest` | max |
| T2 | `code-writer-t2` | `~google/gemini-flash-latest` | medium, the default |
| T3 | `code-writer-t3` | `~google/gemini-pro-latest` | high |

T2 and T3 work test-first: a failing test, then the smallest code that passes it, one piece of behavior at a time. T1 does not. What the tests must cover comes from the brief's `test_scope`, because an isolated executor will stop rather than invent the scope.

Load `code-tier-assessment` before writing the brief and classify the slice against its closed-list criteria; do not eyeball it. Name the tier and, for T1 or T3, the specific box or trigger that put it there, in the brief. A slice with no named trigger for T1 or T3 goes to T2.

## Reading large files

A `PreToolUse` hook may deny whole-file `read` calls above 350 lines (see the end of this skill for whether hooks are active). Plan around it: read with `offset`/`limit` on the region you need, or delegate the reading and take back the summary. When a brief points at a large file, name the region that matters.

## Task brief

Before delegating non-trivial work, compose one structured brief and reuse it for every subagent that needs full context — `code-writer` to implement it, `reviewer` to judge the diff against it. Include:

- `goal`: the one-sentence outcome.
- `slice_id`: a short stable name for this slice, reused verbatim in every brief about it — retries, escalations, and the fix brief after review. When hooks are active, a gate refuses `code-writer-t1/t2/t3` and `test-runner` without a `slice_id:` line in the prompt, and the run log ties runs together by it.
- `tier`: `T1`, `T2`, or `T3`; for `T1` or `T3`, the box or trigger from `code-tier-assessment`.
- `context`: why, plus constraints that shape the approach.
- `files_to_inspect`: the specific files to read before changing anything.
- `changes`: per file, a `file` and a one-line `action` — what must change, not how.
- `risks`: concrete ways the change could break something else.
- `test_scope`: for `T2` and `T3`, the public surface the tests must go through, and which `risks` must be covered as failure modes. When the project has no test setup, say so. Omit for `T1`.
- `acceptance_criteria`: how to verify done (build, specific test, observed behavior).

If something needed for the brief is missing, ask the user instead of guessing. If you filled gaps by inference, summarize the assumptions and get the user to confirm before delegating.

When the work comes from a subagent report — a reviewer finding or a `bug-investigator` diagnosis — forward the report's own section verbatim as the brief, adding only the objective and the files in scope. Carry the reporter's qualifiers: severity, confidence, and a diagnosis status of `PROBABLE` or `INCONCLUSIVE`. Do not delegate a fix from an `INCONCLUSIVE` or `BLOCKED` report.

## Slicing multi-file work

- One slice = one coherent change with its own acceptance criteria and no file shared with another in-flight slice.
- Order slices by dependency: schemas, contracts, and interfaces first; callers after; tests and docs last.
- Run at most one `code-writer` at a time. Only read-only work parallelizes.
- Validate a slice that changes behavior before starting the next one. Never stack unverified slices.
- If you cannot cut clean slices, plan first with `planner` or the `coding-task-planner` skill, or ask the user.

## Work loop

1. Define acceptance criteria and identify material risks.
2. Inspect the repository before researching externally. Before planning anything new, check that it does not already exist — search by domain concept, not by the wording of the request. State where you looked.
3. When the task is a reported defect, reproduce it before planning a fix: delegate to `bug-investigator`, which returns reproduction and root cause from one run. Reproduced → carry the diagnosis into the brief; not reproduced → report and stop; cannot be reproduced here → say so and ask the user.
4. Plan non-trivial work (`planner` only when the approach is genuinely open), then stress-test the plan with the user via the `grilling` skill unless they already answered the same questions or asked to skip.
5. Implement the smallest coherent change: classify with `code-tier-assessment` and delegate to the tier's tool. When its report comes back, take a snapshot (see **Snapshots**).
6. Validate through `test-runner`, forwarding the implementer's `Verification` section as-is; `test-runner` closes the unverified gaps and must not repeat a check that already passed. Name the `slice_id` here too. Around this delegation record `git status --short` and `git diff --binary | shasum` before and after; if `test-runner` changed source or configuration, block the gate and report the paths — do not auto-revert.
7. On failure, find the root cause before fixing; default to `bug-investigator` unless the cause is genuinely one-line and obvious. Never patch only the symptom.
8. From the second failure on the same slice, or the same error twice in a row: record `git status --short` and `git diff --stat`, then classify the failure — `executor` (raise the tier by one, attempt spent), `spec` (back to planning or the user), `env` (fix the environment, retry), `slicing` (recut). Carry the class into the next brief as `previous_failure_class`.
9. A fresh implementer after a failure gets a fresh brief plus a handoff block: `previous_tier`, `previous_failure_class`, `symptom`, `tried`, `ruled_out`, `diagnosis` (verbatim, with severity and confidence), `kept`.
10. After validation passes, call `reviewer` once for non-trivial or high-risk changes, with the brief, the final diff, the test report, and the snapshot ref — not the implementer's reasoning. Name the conditional skills the change calls for.
11. Triage each finding: check for duplicates and known false positives, reject only with evidence, run `finding-verifier` on a doubted finding, forward evidence and fix direction verbatim. Re-review gets the previous findings plus the delta between snapshots.
12. Decide whether the task is complete. A subagent verdict is evidence, not the final decision.

## Snapshots

Implementers never commit, so the work under review exists only in the working tree. After every implementer run, snapshot the working tree into a hidden ref (authorized by the user as a standing exception to "commit only when asked"; never on a branch, not sent by a default push). In the repository root, with `S` set to `<slice_id>/<n>`:

```sh
tmp=$(mktemp -u "${TMPDIR:-/tmp}/snapidx.XXXXXX") && idx=$(git rev-parse --git-path index) && { [ ! -f "$idx" ] || cp "$idx" "$tmp"; } && GIT_INDEX_FILE="$tmp" git add -A && tree=$(GIT_INDEX_FILE="$tmp" git write-tree) && if parent=$(git rev-parse -q --verify HEAD); then sha=$(git commit-tree "$tree" -p "$parent" -m "snapshot $S"); else sha=$(git commit-tree "$tree" -m "snapshot $S"); fi && git update-ref "refs/snapshots/$S" "$sha" && echo "snapshot $S = $sha"; rm -f "$tmp"
```

No `snapshot … = <sha>` line means no snapshot — stop and report it. Restore: `git checkout refs/snapshots/<slice_id>/<n> -- .`. Outside a git repository, copy the touched files to `$TMPDIR` instead, one directory per run.

## Continuing an implementer

Prefer `send_message` to the same implementer when all of these hold: the correction is named (a finding with `file:line`, a specific failing check, or an `env` classification); the slice and tier are unchanged; the working tree still holds exactly what that agent left; and its previous run ended with a complete report. Start fresh for `executor`, `spec`, and `slicing` failures. A continuation message carries the verdict verbatim, one line confirming the tree is unchanged, and the same end condition. A continuation counts as an implementer run toward the loop limits.

## Completion gate

Finish only when all applicable conditions hold:

- requirements and acceptance criteria are satisfied;
- relevant checks actually passed, and the closing check ran through `test-runner`;
- no unresolved critical, high, or medium review findings remain;
- the fix addresses the diagnosed root cause;
- Trino or analytical SQL was reviewed for correctness and cluster cost;
- a security-sensitive surface was reviewed by `reviewer` with `appsec-review` loaded;
- no validation was disabled or weakened merely to pass;
- no temporary debugging artifacts remain;
- unverified assumptions and limitations are disclosed.

Do not claim a check passed unless it was executed.

## Loop limits

- Attempts per tier: 2 at T1, 2 at T2, 2 at T3, then stop and report `PARTIAL` or `BLOCKED`. An attempt is spent only on a failing `test-runner` or `reviewer` verdict.
- Hard ceiling of 9 implementer runs on one slice, counting every class and every continuation. Keep this count in the brief (`attempt`, `run`) — nothing here reconstructs it for you.
- At most 3 implementation-review cycles; stop earlier after two cycles with no measurable progress.
- Do not rerun a subagent with materially identical input unless new evidence exists.

## Unusable returns

A valid `PARTIAL` is a legitimate report. An empty report is unusable; a malformed one is unusable but not proof of interruption. After a `code-writer` return of either kind, run `git status` and `git diff --stat` first — the tree may hold edits no report mentioned. Re-delegate narrower, never identically. Nothing an unusable report claimed to have verified counts as verified.

## What DSH does and does not enforce

- **Hooks** run only if the profile mounts `@deepseek-ai/dsh-hooks-claude-code` with `~/.claude/hooks/dsh-hooks.json`, through the adapter `~/.claude/hooks/dsh_adapter.py`. When active they cover: Trino project scope (the DDL/DML guard lives in the project's own repo hook and does not run here), protected-config edits, whole-file reads over 350 lines, and the delegation gate (doctrine loaded + `slice_id`) on the ten role tools and on the generic `subagent` tool. `subagent_fork` is disabled in the profile. Whether hooks fire for tool calls made inside a subagent is unverified.
- **Delegation gate:** it remembers that `task-orchestration` was loaded in `/tmp/dsh-claude-state-<uid>/` per session. A subagent has its own session, so it loads the skill itself if it delegates. If that state cannot be written, gated calls are denied with the path named — fix the path, do not route around the gate.
- **Not enforced at all here:** the reviewer's read-only `bash` allowlist (DSH tool events do not say which role made the call). The `reviewer` must keep to read-only commands by its own instructions.
- **Run log:** when hooks are active, role calls are logged with role, `slice_id`, and report-contract status — no tokens, no turns. Under DSH's default hook sandbox `~/.claude/logs` is not writable, so the log lands in `/tmp/dsh-claude-state-<uid>/dsh-subagent-runs.jsonl`, which macOS may clean. `agent-stats.py` and `first-pass.py` do not read it.
- **Tool filters** per role come from the profile. Whether they also hide preset-scoped tools is unverified; do not rely on them as the only guard.
