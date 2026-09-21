---
name: task-orchestration
description: orchestrate non-trivial tasks with specialized OpenCode subagents. use when work needs multiple steps, delegation to subagents, implementation plus verification, independent review, root-cause debugging, external research, or trino/sql optimization.
---

# Task orchestration (OpenCode)

OpenCode-specific override of the Claude Code skill of the same name. Local
`~/.config/opencode/skills/task-orchestration/SKILL.md` shadows the shared
`~/.claude/skills/task-orchestration/SKILL.md` for this agent, because the
mechanics below (models, delegation tool, turn budgets, continuation) differ
from Claude Code and the shared file would otherwise point at things that
don't exist here. See **OpenCode-specific notes** at the end for exactly what
changed and why.

Act as the task owner. Keep requirements, decisions, implementation, and the final completion decision in the main context. Delegate only focused work that benefits from isolation or specialization.

## Decide whether to orchestrate

Handle trivial, obvious, low-risk changes directly.

Use this workflow when the task has multiple steps, uncertain behavior, meaningful risk, noisy validation, external research, or requires independent review.

## Delegate narrowly

Give every subagent only:

- the exact objective;
- relevant files, commands, versions, and constraints;
- the names of applicable skills to load (never their pasted content);
- required evidence;
- the expected compact output;
- the condition that ends the subtask.

Do not pass the full conversation, unrelated files, or raw historical logs. Ask for conclusions, evidence, and actionable findings.

Use subagents by role (`task` tool, `subagentType` argument — the exact agent file name under `~/.config/opencode/agent/`):

- `web-researcher-fast`: narrow factual or documentation lookup;
- `docs-researcher-deep`: ambiguous, version-sensitive, or high-impact research;
- `test-runner`: focused tests, linters, type checks, and build checks;
- `bug-investigator`: root-cause analysis after a non-obvious or repeated failure;
- `code-writer`: focused implementation of a single well-scoped feature slice, fix, or refactor; runs at three tiers (`code-writer-t1/t2/t3`), see **Executor tiers** and the `code-tier-assessment` skill;
- `code-reviewer`: adversarial review of non-trivial code changes;
- `sql-data-reviewer`: Trino and analytical SQL correctness and resource review;
- `finding-verifier`: verify one specific claim (a review finding, a suspected diagnosis) before spending a `code-writer` run on it — read-only, refutation-first, never hunts for other defects.

There is no OpenCode equivalent of Claude Code's `Explore` or `general-purpose` built-in agents. For broad read-only search, read files or `grep` directly in the main context, or delegate to `docs-researcher-deep`/`web-researcher-fast` when it is genuinely a research question rather than a codebase-location question. OpenCode's `plan` agent mode is a primary session mode you switch into, not a subagent you delegate to — reach for it (outside this skill's own mechanics) when the implementation approach is genuinely open, ahead of writing the brief below.

Do not re-read in the main context what a subagent already summarized.

Run subagents in parallel only when their work is independent and read-only. Never let parallel workers edit the same files.

## Budgets

Every delegation costs tokens and wall-clock, and every report lands back in your context. Spend deliberately.

- Match the model to the question, not to the role. Each agent file sets a fixed `model` and `reasoning.effort` (or `options.reasoningEffort`) — whether the `task` tool accepts a per-call model override the way Claude Code's `Agent` call does is unverified for this OpenCode version; treat the agent file's default as fixed unless you've confirmed otherwise. `code-writer` needs effort to vary too, so it ships as three separate agent files instead of one model override — see **Executor tiers**.
- Know what the heavy roles cost before reaching for one. `code-reviewer` and `sql-data-reviewer` run `openrouter/qwen/qwen3.8-max` at `xhigh` effort. Right for a risky diff, wasteful for a typo.
- At most 3 subagents in flight at once, and only when each answers a different question. Parallel work multiplies spend immediately and drops several reports into your context at the same moment.
- Reach for `web-researcher-fast` over `docs-researcher-deep` whenever one authoritative source settles the question — both are cheap (`dspark/deepseek-v4-flash`, low effort), but `docs-researcher-deep` runs `openrouter/~google/gemini-pro-latest` at `high` effort and costs more.
- Never pay twice for the same evidence. A check `code-writer` ran and reported does not get rerun by `test-runner`, and a file a subagent summarized does not get re-read in the main context.
- Cost and report state are measured, not guessed: `python3 ~/.config/opencode/tools/agent-stats.py` (or `/agent-stats`) reads `~/.claude/logs/subagent-runs-opencode.jsonl`, written by the `claude-hooks.js` plugin. A `max_tokens`-class stop is real truncation evidence; this log has no `maxTurns` field at all (OpenCode agent frontmatter doesn't define one), so there is no equivalent "budget warning" signal to distinguish from truncation — treat an unfinished-looking report as unverified per **Unusable and truncated returns**, not as proof of a turn-limit hit.

## Executor tiers

`code-writer` ships as three separate agent files, one per tier — `code-writer-t1`, `code-writer-t2`, `code-writer-t3` — because effort differs between them.

| Tier | Agent | Model | Effort | Target share |
|---|---|---|---|---|
| T1 | `code-writer-t1` | dspark/deepseek-v4-flash | high | ~30% |
| T2 | `code-writer-t2` | openrouter/~deepseek/deepseek-v4.1-flash-latest | medium | ~50%, the default |
| T3 | `code-writer-t3` | openrouter/~google/gemini-flash-latest | xhigh | ~20% |

`openrouter/qwen/qwen3.8-max` never writes code — it stays reserved for `code-reviewer` and `sql-data-reviewer`.

T2 and T3 work test-first: a failing test, then the smallest code that passes it, one piece of behavior at a time. T1 does not — a red-green cycle on a one-line fix doubles the cost of the cheapest tier for nothing. The rules live in those two agent files; what they must cover comes from the brief's `test_scope`, because an isolated executor cannot ask you mid-run and will stop rather than invent the scope.

Load `code-tier-assessment` before writing the brief and classify the slice against its closed-list criteria; do not eyeball it. Name the tier and, for T1 or T3, the specific box or trigger that put it there, in the brief. A slice with no named trigger for T1 or T3 goes to T2.

There is no OpenCode equivalent of `first-pass.py` yet — actual tier routing can only be checked by hand from `subagent-runs-opencode.jsonl` (`tier` field). Target shares above are a calibration goal, not a quota to force on any single task.

## Reading large files

The `claude-hooks.js` plugin denies whole-file `read` calls above 350 lines (threshold: `OPENCODE_READ_LINE_LIMIT`). Plan around it instead of spending a turn on the refusal:

- in the main context, call `read` with `offset`/`limit` on the region you actually need — there is no `Explore` agent to delegate a broad read to here;
- when a brief points at a large file, name the region that matters (`file:line` range, symbol, or function) — subagents have no delegation tool of their own and can only fall back to targeted reads.

## Task brief

Before delegating non-trivial work, compose one structured brief and reuse it for every subagent that needs full context — `code-writer` to implement it, `code-reviewer` and `sql-data-reviewer` to judge the diff against it instead of guessing intent from the code alone. Act like a lead scoping work for an implementer, not writing the code yourself. Include:

- `goal`: the one-sentence outcome.
- `slice_id`: a short stable name for this slice, reused verbatim in every brief about it — retries, escalations, and the fix brief after review. It is what ties the implementer run, its validation and its review together in the run log; without it a slice's outcome cannot be measured afterwards.
- `tier`: `T1`, `T2`, or `T3`; for `T1` or `T3`, the box or trigger from `code-tier-assessment` that put it there.
- `context`: why, plus constraints that shape the approach.
- `files_to_inspect`: the specific files to read before changing anything.
- `changes`: per file, a `file` and a one-line `action` — what must change, not how.
- `risks`: concrete ways the change could break something else.
- `test_scope`: for `T2` and `T3`, the public surface the tests must go through, and which of the `risks` above must be covered as failure modes. Both tiers work test-first and will stop rather than guess when the code offers more than one plausible surface, so name it. Breadth is your call, not theirs: happy path plus the named failure modes, not an exhaustive sweep of edge cases. When the project has no test setup, say so here — the implementer will not build one on its own initiative. Omit for `T1`.
- `acceptance_criteria`: how to verify done (build, specific test, observed behavior).

Three more fields say where the work happens and what lets it rejoin the rest. For an ordinary sequential slice they are one line each and mostly formalities; they carry weight only once a slice runs isolated.

- `worktree`: the directory the implementer works in. `main checkout` for a sequential slice. For an isolated slice, the worktree path — and note that a subagent worktree branches from the **default branch**, not from `HEAD`, so uncommitted work in the main checkout and a previous slice's result are both invisible inside it. Commit the main checkout before launching anything isolated, or the slice starts from a state that does not exist.
- `branch`: the branch that will hold this slice's commits. `current` for a sequential slice. For an isolated one, a branch of its own named from the `slice_id`, because git refuses to check the same branch out in two worktrees at once.
- `merge_gate`: the exact condition that allows this slice to be integrated, when that is more than `acceptance_criteria`. For an isolated slice: its own criteria met, the branch merges without conflict, and the suite passes **after** the merge. A conflict here is not git doing its job — it is proof the slicing was wrong, two slices claimed the same file, and it is classified `slicing`, not `executor`. Omit for a sequential slice, where acceptance and integration are the same event.

Naming `branch` does not by itself let anyone write to it: `code-writer-t1/t2/t3` are all forbidden to commit. Until that changes, fill these three as a record of where the work ran, not as an instruction to an implementer.

Do not implement the change yourself once a brief like this is possible — that is `code-writer`'s job.

Before handing it off:
- If something needed for the brief is missing (unclear goal, unknown files, no acceptance criteria), ask the user directly instead of guessing.
- If you filled gaps by inference, do not proceed silently. Summarize the assumptions against the structure above and get the user to confirm before delegating.

When the work comes from a subagent report — a reviewer finding or a `bug-investigator` diagnosis — do not author a fresh brief and do not summarize. Forward the report's own section verbatim as the brief, adding only the objective and the files in scope. Carry the reporter's qualifiers with it: severity, confidence, and a diagnosis status of `PROBABLE` or `INCONCLUSIVE`. Never present a hedged diagnosis as established, and do not delegate a fix from an `INCONCLUSIVE` or `BLOCKED` report. When a finding is doubted, run it through `finding-verifier` first rather than spending a `code-writer` run to find out it didn't hold.

## Slicing multi-file work

`code-writer` takes one area at a time. When the change spans several, cut it into slices yourself before delegating:

- one slice = one coherent change with its own acceptance criteria and no file shared with another in-flight slice. If two slices cannot avoid the same file, they are one slice.
- order slices by dependency: schemas, contracts, and interfaces first; callers after; tests and docs last.
- run at most one `code-writer` at a time — slices are sequential by default. Only read-only work parallelizes: the researchers, `finding-verifier`, and reviewers judging a diff that has stopped moving.
- validate a slice that changes behavior before starting the next one. Never stack unverified slices.
- write the brief per slice, not per feature. A brief whose `changes` list keeps growing is still too big to hand off.
- if you cannot cut clean slices, the task is not ready to delegate: plan it first (switch to the `plan` agent mode, or think it through in the main context), or ask the user.

## Work loop

1. Define acceptance criteria and identify material risks.
2. Inspect the relevant repository context before researching externally. Before planning anything new, check that it does not already exist. Search by domain concept, not by the wording of the request — the project may call the same thing something else. A targeted grep or read in the main context is enough when you already know the vocabulary; there is no broad-search subagent to delegate this to here. State where you looked. If the behavior already exists, report that instead of building it.
3. When the task is a reported defect, reproduce it before planning a fix. Delegate to `bug-investigator` — reproduction and root cause come back from the same run, so never split them across two delegations or repeat this work at step 7. Run the check yourself only when it is a single command whose output is short; anything noisier belongs inside the subagent. Three outcomes:
   - reproduced — carry the diagnosis, with its severity and confidence, into the plan and the brief;
   - not reproduced — report that to the user and stop; do not fix a defect you could not observe;
   - cannot be reproduced here (missing environment, production data, cluster access) — say so plainly rather than substituting a weaker check, and ask the user how to proceed.
4. Create a short plan for non-trivial work, then stress-test it with the user via the `grilling` skill before building. This is the default, not a step reserved for risky plans: the questions are cheapest before any code exists, and a plan that survives them needs no rework later. Skip it only when the user has already answered the same questions in this session, or explicitly asks you to skip.
5. Implement the smallest coherent change — directly in the main agent for trivial work, or classify it with `code-tier-assessment` and delegate to the tier's `code-writer-t1/t2/t3` agent with the brief above for anything non-trivial.
6. Run validation through `test-runner`, forwarding the `Verification` section of the implementer's report as-is — the commands already run with their outcomes, and everything it listed as not verified. `test-runner` closes the unverified gaps and the canonical check; it must not repeat a check that already passed. The check that closes a slice always runs here, never in the main context: a check nobody recorded is a claim rather than a gate, and a slice whose gate left no row behind cannot be judged afterwards. Exploratory checks — a grep, a quick sanity run while still deciding what to do — stay wherever they are cheapest; simply do not pre-run the closing check yourself, or `test-runner` is left with nothing to close. Name the slice's `slice_id` in this delegation too: validation is part of the slice's record, and a run without it cannot be attributed to the slice it validated.
7. On failure, identify the root cause before fixing. If step 3 already diagnosed this defect, build on that verdict instead of re-running the investigation. Fix directly only when the cause is genuinely one-line and obvious; default to `bug-investigator` otherwise — if you are still deciding whether it counts as obvious, it does not, delegate. Give it the failing command and concise evidence. Never patch just the symptom (suppressing an error, adding a defensive check, retrying) without confirming why the failure actually occurs.
8. From the second consecutive failure on the same slice, classify the failure before doing anything else. `bug-investigator` returns a root cause only — it never saw the brief and does not know how the work was sliced, so it cannot judge whether the spec is complete or the slice too large. You own the classification; map the diagnosis against your own brief:
   - `executor` — the implementer was not capable enough: raise the tier, attempt spent;
   - `spec` — the brief is incomplete or self-contradictory: return to planning or ask the user, same tier, attempt not spent;
   - `env` — flake, broken fixture, missing environment: fix the environment and retry, same tier, attempt not spent;
   - `slicing` — the slice is too large: recut it, same tier, counter reset.

   Carry the class into the next brief for that slice as `previous_failure_class`, whichever class it was — the next run is then logged against what it is retrying.

   Raising the tier only cures missing capability. A stronger model or more effort does not repair a contradictory requirement — it invents a confident reading of it.
9. Escalate a tier only on an `executor` classification, one tier at a time (T1→T2 or T2→T3, never skipping), and only onto a clean slate:
   - clean the working tree first. Run `git status` and `git diff --stat`, keep only the coherent verifiable subset, revert the rest. Otherwise the next tier builds on the previous one's leftovers — the same contamination, in files instead of context.
   - write a fresh brief and append a handoff block — do not forward the transcript:
     - `previous_tier`: the tier that failed;
     - `previous_failure_class`: `executor`;
     - `symptom`: the command and its output, truncated;
     - `tried`: what was changed, with what result;
     - `ruled_out`: hypotheses eliminated, and by what evidence;
     - `diagnosis`: the `bug-investigator` verdict verbatim, with its severity and confidence;
     - `kept`: which edits remain in the working tree.

   A transcript of failed attempts carries the frame the previous implementer was stuck in, not the facts.
10. After validation passes, hand the same brief to the reviewer alongside the diff, then use:
   - `sql-data-reviewer` for meaningful Trino or analytical SQL changes;
   - `code-reviewer` for non-trivial or high-risk code changes.
11. Triage each review finding without re-deriving it: check it isn't a duplicate or an already-rejected false positive, reject only with evidence, then forward the reviewer's evidence and fix direction verbatim — do not paraphrase or re-diagnose. When a finding is doubted, run `finding-verifier` on it before triaging further. Send it to the implementer that wrote the slice when **Continuation, and its limits** below allows it; otherwise it becomes the brief for a fresh `code-writer`. Rerun affected validation and review afterward.
12. Decide whether the task is complete. A subagent verdict is evidence, not the final decision.

## Continuation, and its limits

Claude Code can reach a finished subagent again with `SendMessage`, keeping everything it read and wrote in its context. Whether OpenCode's `task` tool has an equivalent — resuming a specific prior subagent session rather than starting a new one — is unverified for this setup. Until confirmed, start fresh for every retry: write a fresh brief carrying the step 9 handoff block (`tried`, `kept`, `ruled_out`) so the new implementer knows the code already on disk is a previous run's work, not its own to reinterpret from scratch.

If a resume mechanism turns out to exist, it is only safe to use under the same conditions Claude Code requires: the correction is named (a reviewer finding with `file:line`, a specific failing check, an `env` classification), the slice and tier are unchanged, and the working tree still holds exactly what that run left — nothing reverted, no other slice landed, nothing edited by hand. When in doubt, start fresh regardless.

## Completion gate

Finish only when all applicable conditions hold:

- explicit requirements and acceptance criteria are satisfied;
- relevant checks actually passed, and the check that closed the slice ran through `test-runner`, so the slice has a recorded gate rather than a claim;
- no unresolved critical, high, or medium review findings remain;
- the fix addresses the diagnosed root cause, not just the visible symptom;
- Trino or analytical SQL was reviewed for correctness and cluster cost;
- a security-sensitive surface (authentication, secrets, permissions, untrusted input, data exposure) was reviewed as such — via `code-reviewer` with a security-focused brief (there is no `security-review` skill available to this agent; it is a Claude Code plugin skill, not filesystem-discoverable here);
- no validation was disabled or weakened merely to pass — `claude-hooks.js`'s config-protection check enforces this for known linter/type-checker configs, but is not a substitute for judgment on everything else;
- no temporary debugging artifacts remain, and scratchpad files are not referenced from code, docs, or the final answer;
- unverified assumptions and limitations are disclosed.

Do not claim a check passed unless it was executed.

## Loop limits

- If a check fails with the same error 2 times in a row: stop editing, revert the last code change, widen the scope of analysis (question earlier assumptions, pull in more context, escalate to `bug-investigator` if not already), and formulate an alternative plan before resuming.
- Attempts per tier: 2 at T1, then 2 at T2, then 2 at T3, then stop and report `PARTIAL` or `BLOCKED`. An attempt is spent only on a failing `test-runner` or `code-reviewer` verdict — never on the implementer's own account of its work, and never on a `spec`, `env`, or `slicing` classification.
- Hard ceiling of 9 implementer runs on one slice, counting every failure class. Without it the classes that do not spend an attempt loop forever. Keep this count yourself — `subagent-runs-opencode.jsonl` has no notion of a continuation vs. a fresh run, so the log cannot be trusted to hold the tally.
- Allow at most 3 implementation-review cycles.
- Stop earlier if two consecutive cycles produce no measurable progress.
- Do not rerun a subagent with materially identical input unless new evidence exists.
- When limits are reached, stop speculative edits and report `PARTIAL` or `BLOCKED` with the remaining issue and next concrete action.

## Unusable and truncated returns

Keep three states separate. A valid `PARTIAL` is a legitimate report: it may close a bounded partial step, but its stated remainder still needs work. An empty or unavailable report is unusable. A malformed report (wrong leading status or missing required section) is also unusable, but does not by itself prove the work was interrupted.

Treat a return as actually truncated only with termination evidence such as `stop_reason: max_tokens`, an explicit timeout/error, or another clear signal from the provider. An unfinished-looking sentence or a bad report format is not proof; at most label it a suspected interruption and keep that uncertainty visible. OpenCode agent frontmatter has no `maxTurns` field, so there is no separate "budget warning" signal to rule out here the way Claude Code's `maxTurns` is ruled out — absence of evidence stays absence of evidence, not proof either way.

Recover in this order:

1. Establish the real state before deciding anything. After `code-writer`, run `git status` and `git diff --stat`: the working tree may hold edits no report ever mentioned. Read-only agents leave nothing behind — only their turns are lost.
2. For empty or unavailable reports, do not close the step. For a malformed report with usable material, ask the same agent to reformat or summarize that material before redoing work; do not automatically reslice, roll back, or repeat the investigation.
3. Inspect writer edits before choosing to keep or revert. Revert to the last clean state only when edits are incoherent or unverifiable. Keep a coherent subset you can name, and treat the remainder as the next slice.
4. For confirmed truncation, re-delegate narrower, never identically. Cut the slice down, list files already changed as done, and state exactly what remains. After a second confirmed truncation on the same slice, question the slicing.
5. Count confirmed truncations and failed recovery attempts against the attempt budget above. Nothing an unusable report claimed to have verified counts as verified.

## Long artifacts

Subagent reports are capped at 450-1,300 words. When the substance genuinely does not fit — a long diff, a full plan, a wide findings table, a captured log — have the subagent write the bulk to a file and return the path plus the summary that does fit.

- Name the exact path in the brief. Use the session scratchpad directory from the environment prompt, never the project tree.
- The report still carries the verdict, severities, evidence, and next action. The file holds detail, never the decision.
- Open that file only when a decision depends on the detail, and read it with `offset`/`limit`.
- Treat these files as disposable: never hand one to the user as a deliverable, and never reference one from code or docs.

## Context hygiene

Keep raw logs inside `test-runner` or `bug-investigator`, broad searches inside research agents, and detailed objections inside reviewers. Bring back only decisions, evidence, and unresolved actions.

In the final response state:

1. result: completed, partial, or blocked;
2. changed files and behavior;
3. exact validation performed;
4. relevant review verdicts;
5. remaining limitations or risks;
6. subagent ledger: for every subagent type used in this task, its (model, effort), tokens burned, turns run, and success share (fraction of its own runs that ended in a passing verdict) — read from `~/.claude/logs/subagent-runs-opencode.jsonl` filtered to this task's `slice_id`(s), or `python3 ~/.config/opencode/tools/agent-stats.py` (or `/agent-stats`) for a broader window. Read the log; do not recall the delegation from memory.

## OpenCode-specific notes

What changed from the shared Claude Code skill, and why, so a future edit here or there can tell divergence from drift:

- **Delegation tool**: Claude Code's `Agent` tool → OpenCode's `task` tool, `subagentType` argument.
- **Model names**: `haiku`/`sonnet`/`opus` → this setup's actual models (`dspark/deepseek-v4-flash`, `openrouter/~deepseek/deepseek-v4.1-flash-latest`, `openrouter/~google/gemini-flash-latest`, `openrouter/qwen/qwen3.8-max`) — see `~/.config/opencode/agent/*.md` and `opencode.jsonc`.
- **No `Explore`, no `general-purpose`, no delegatable `Plan`**: OpenCode's agent set here has no broad-search or catch-all subagent. `plan` is a primary session mode, not something the `task` tool can spawn.
- **No `maxTurns`, no verified continuation**: OpenCode agent frontmatter carries no turn cap, and whether a subagent session can be resumed (Claude Code's `SendMessage`/`ListAgents`) is unverified here — see **Continuation, and its limits**.
- **No `first-pass.py`, no `security-review` skill**: not ported; `agent-stats.py` and `context-budget.py` exist at `~/.config/opencode/tools/`.
- **Read-line guardrail, orchestration gate, subagent logging**: all live in the `claude-hooks.js` plugin (`~/.config/opencode/plugins/`), not in separate hook scripts — one file, `tool.execute.before`/`tool.execute.after`.
- If you find a new mismatch between this file and how OpenCode actually behaves, fix it here rather than assuming the shared Claude Code text was right — that's the whole reason this override exists.
