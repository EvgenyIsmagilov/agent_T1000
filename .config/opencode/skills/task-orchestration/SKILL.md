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

Act as the task owner. Keep requirements, decisions, and the final completion decision in the main context. Delegate only focused work that benefits from isolation or specialization.

## Decide whether to orchestrate

`CLAUDE.md` defines what counts as code and who edits it: only a `code-writer` tier, never you, however small the change. A trivial change goes to `code-writer-t1` with a short brief. Answer read-only questions, research, and document-only work directly or through a researcher.

Use the full workflow below when the task has multiple steps, uncertain behavior, meaningful risk, noisy validation, external research, or requires independent review.

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

- `planner`: read-only implementation plan with slices, risks, and acceptance criteria, for a task whose approach is genuinely open — ahead of writing the brief below;
- `web-researcher`: narrow factual or documentation lookup; if unsure which researcher fits, take this cheaper one;
- `docs-researcher`: ambiguous, version-sensitive, or high-impact research;
- `test-runner`: focused tests, linters, type checks, and build checks;
- `bug-investigator`: root-cause analysis after a non-obvious or repeated failure;
- `code-writer`: focused implementation of a single well-scoped feature slice, fix, or refactor; runs at three tiers (`code-writer-t1/t2/t3`), see **Executor tiers** and the `code-tier-assessment` skill;
- `reviewer`: the single adversarial review gate. It always loads `code-review`, adds `sql-review` and `sql` for meaningful Trino or analytical SQL, `appsec-review` for a security-sensitive surface, and `airflow`/`python` where the change needs them — one run, one verdict;
- `finding-verifier`: verify one specific claim (a review finding, a suspected diagnosis) before spending a `code-writer` run on it — read-only, refutation-first, never hunts for other defects.

There is no OpenCode equivalent of Claude Code's `Explore` or `general-purpose` built-in agents. For broad read-only search, read files or `grep` directly in the main context, or delegate to `docs-researcher`/`web-researcher` when it is genuinely a research question rather than a codebase-location question. For an implementation approach that is genuinely open, delegate to `planner`; OpenCode's own `plan` agent mode is a primary session mode you switch into, not a subagent.

Do not re-read in the main context what a subagent already summarized.

Run subagents in parallel only when their work is independent and read-only. Never let parallel workers edit the same files.

## Budgets

Every delegation costs tokens and wall-clock, and every report lands back in your context. Spend deliberately.

- Match the model to the question, not to the role. Each agent file sets a fixed `model` and `reasoning.effort` (or `options.reasoningEffort`) — whether the `task` tool accepts a per-call model override the way Claude Code's `Agent` call does is unverified for this OpenCode version; treat the agent file's default as fixed unless you've confirmed otherwise. `code-writer` needs effort to vary too, so it ships as three separate agent files instead of one model override — see **Executor tiers**.
- Know what the heavy roles cost before reaching for one. `reviewer` runs `openrouter/google/gemini-4-argon` at `xhigh` effort with `steps: 120`. Every slice that changed code still gets one `reviewer` run (Work loop step 10), so on a small diff keep its brief and target narrow instead of skipping it.
- At most 10 subagents in flight at once, and only when each answers a different question. Parallel work multiplies spend immediately and drops several reports into your context at the same moment.
- Reach for `web-researcher` over `docs-researcher` whenever one authoritative source settles the question — `web-researcher` runs `dspark/deepseek-v4-flash` (local Spark, thinking mode not configured), `docs-researcher` runs `openrouter/deepseek/deepseek-v4.1-flash-1017` at `high` effort and costs more.
- Never pay twice for the same evidence. A check `code-writer` ran and reported does not get rerun by `test-runner`, except the slice's closing check (Work loop step 6), and a file a subagent summarized does not get re-read in the main context.
- Cost and report state are measured, not guessed: `python3 ~/.config/opencode/tools/agent-stats.py` (or `/agent-stats`) reads `~/.claude/logs/subagent-runs-opencode.jsonl`, written by the `claude-hooks.js` plugin. A `max_tokens`-class stop is real truncation evidence; this log has no `maxTurns` field at all (OpenCode agent frontmatter doesn't define one), so there is no equivalent "budget warning" signal to distinguish from truncation — treat an unfinished-looking report as unverified per **Unusable and truncated returns**, not as proof of a turn-limit hit.

## Executor tiers

`code-writer` ships as three separate agent files, one per tier — `code-writer-t1`, `code-writer-t2`, `code-writer-t3` — because effort differs between them.

Model, effort, target share per tier, and the criteria for each tier live in `code-tier-assessment`; the agent files are the source of truth for the settings.

T2 and T3 work test-first: a failing test, then the smallest code that passes it, one piece of behavior at a time. T1 does not — a red-green cycle on a one-line fix doubles the cost of the cheapest tier for nothing. The rules live in those two agent files; what they must cover comes from the brief's `test_scope`, because an isolated executor cannot ask you mid-run and will stop rather than invent the scope.

Load `code-tier-assessment` before writing the brief and classify the slice against its closed-list criteria; do not eyeball it. Name the tier and, for T1 or T3, the specific box or trigger that put it there, in the brief. A slice with no named trigger for T1 or T3 goes to T2.

## Reading large files

The `claude-hooks.js` plugin denies any `read` that would return more than 350 lines (threshold: `OPENCODE_READ_LINE_LIMIT`): a whole file, or an `offset`/`limit` range. The denial lists a line outline of the file only when the file is inside the session working directory. Plan around it instead of spending a turn on the refusal:

- in the main context, call `read` with `offset`/`limit` on the region you actually need — there is no `Explore` agent to delegate a broad read to here;
- when a brief points at a large file, name the region that matters (`file:line` range, symbol, or function) — subagents have no delegation tool of their own and can only fall back to targeted reads.

## Task brief

Before delegating non-trivial work, compose one structured brief and reuse it for every subagent that needs full context — `code-writer` to implement it, `reviewer` to judge the diff against it instead of guessing intent from the code alone. Act like a lead scoping work for an implementer, not writing the code yourself. Include:

- `goal`: the one-sentence outcome.
- `slice_id`: a short stable name for this slice, reused verbatim in every brief about it — retries, escalations, and the fix brief after review. A hook refuses to spawn `code-writer-t1/t2/t3` or `test-runner` without a `slice_id:` line in the prompt. It is what ties the implementer run, its validation and its review together in the run log; without it a slice's outcome cannot be measured afterwards.
- `tier`: `T1`, `T2`, or `T3`; for `T1` or `T3`, the box or trigger from `code-tier-assessment` that put it there.
- `context`: why, plus constraints that shape the approach.
- `files_to_inspect`: the specific files to read before changing anything. For `T2` and `T3`, add an anchor for each place you already read while you scoped the slice: `file:start-end — symbol — what changes or matters there`. Write only ranges you actually saw; never guess line numbers. If a file changed after you read it (an earlier slice, a fix round), read the range again or give the symbol without line numbers. Anchors are a starting point, not a boundary: the implementer still reads callers, tests, and anything else the change needs.
- `changes`: per file, a `file` and a one-line `action` — what must change, not how.
- `risks`: concrete ways the change could break something else.
- `invariant_paths`: for `T2` and `T3`, every code path and twin implementation (for example a C# copy and a GDScript copy) that holds the invariant this slice touches. Find them by search, not from memory. If there is only one, write `none` and the reason. A plugin hook refuses to spawn `code-writer-t2` or `code-writer-t3` without this line, `data_sample`, `spec_check`, and `divisible`.
- `data_sample`: for `T2` and `T3`, the real sample (a file, a record, a query result) that your assumptions about an external data format were checked against. Write `n/a` if the slice reads no external format.
- `spec_check`: for `T2` and `T3`, the ticket or requirement you compared the brief with, and a statement that the brief contradicts neither it nor itself. If there is no ticket, write `none` and the reason.
- `divisible`: for `T2` and `T3`, `yes` or `no`, then the reason. Example: `divisible: no — one parser, every change goes through the same function`. Apply the test in **Slicing multi-file work**. The tier records how difficult the slice is; this field records only whether you can cut it. A plugin hook checks this line for `code-writer-t2/t3`. It refuses the call if the value does not start with `yes` or `no` or has no reason on the same line.
- `volume`: for `T2` and `T3`, the number of files in `changes` and the total line count of the files in `files_to_inspect`. Count the lines with `wc -l`. Example: `volume: 9 files, 4200 lines`. **Slicing multi-file work** tells you what to do with these numbers.
- `test_scope`: for `T2` and `T3`, the public surface the tests must go through, and which of the `risks` above must be covered as failure modes. Both tiers work test-first and will stop rather than guess when the code offers more than one plausible surface, so name it. Breadth is your call, not theirs: happy path plus the named failure modes, not an exhaustive sweep of edge cases. When the project has no test setup, say so here — the implementer will not build one on its own initiative. Omit for `T1`.
- `acceptance_criteria`: how to verify done (build, specific test, observed behavior). A criterion checks behavior on a fixture or a controlled input, not a snapshot of live data.

Before handing it off:
- If something needed for the brief is missing (unclear goal, unknown files, no acceptance criteria), ask the user directly instead of guessing.
- If you filled gaps by inference, do not proceed silently. Summarize the assumptions against the structure above, inferred `acceptance_criteria` included, and get the user to confirm before delegating.

When the work comes from a subagent report — a reviewer finding or a `bug-investigator` diagnosis — do not author a fresh brief and do not summarize. For a `T2` or `T3` slice, still copy the slice's `invariant_paths`, `data_sample`, `spec_check`, and `divisible` lines into it. Forward the report's own section verbatim as the brief, adding only the objective and the files in scope. Carry the reporter's qualifiers with it: severity, confidence, and a diagnosis status of `PROBABLE` or `INCONCLUSIVE`. Never present a hedged diagnosis as established, and do not delegate a fix from an `INCONCLUSIVE` or `BLOCKED` report. When a finding is doubted, run it through `finding-verifier` first rather than spending a `code-writer` run to find out it didn't hold.

### Isolated slices (optional)

Omit these three fields for an ordinary sequential slice. Add them only when a slice runs in its own worktree:

- `worktree`: the directory the implementer works in. The worktree path — and note that a subagent worktree branches from the **default branch**, not from `HEAD`, so uncommitted work in the main checkout and a previous slice's result are both invisible inside it. Commit the main checkout before launching anything isolated, or the slice starts from a state that does not exist.
- `branch`: the branch that will hold this slice's commits. A branch of its own named from the `slice_id`, because git refuses to check the same branch out in two worktrees at once.
- `merge_gate`: the exact condition that allows this slice to be integrated, when that is more than `acceptance_criteria`. For an isolated slice: its own criteria met, the branch merges without conflict, and the suite passes **after** the merge. A conflict here is not git doing its job — it is proof the slicing was wrong, two slices claimed the same file, and it is classified `slicing`, not `executor`.

Naming `branch` does not by itself let anyone write to it: `code-writer-t1/t2/t3` are all forbidden to commit. Until that changes, fill these three as a record of where the work ran, not as an instruction to an implementer.

## Slicing multi-file work

`code-writer` takes one area at a time. When the change spans several, cut it into slices yourself before delegating:

- one slice = one coherent change with its own acceptance criteria and no file shared with another in-flight slice. If two slices cannot avoid the same file, they are one slice.
- a slice is divisible only if each part of the cut meets three conditions. Each part has its own acceptance criteria. No part shares a file with another part. All paths from `invariant_paths` are in the same part. Never cut across an invariant. Two slices that each change half of one invariant leave the tree broken between them. A small slice with difficult logic is `divisible: no` and stays whole. Its difficulty goes into the tier, not into a cut.
- measure the volume of each `T2` or `T3` slice before you delegate it, and write it in the `volume` line. If a `divisible: yes` slice crosses a threshold, cut it before you delegate it. The thresholds are more than 8 files in `changes` or more than 3000 lines to read. These thresholds are starting values and are not calibrated yet. If the slice is `divisible: no`, do not cut it. Do these steps instead:
  1. Give an anchor in `files_to_inspect` for each place that the slice changes.
  2. If the files to read are over the threshold, have one cheap read-only subagent summarize them first. Put the summary in `context`.
  3. If the approach is open, run `planner` before you write the brief.
- after a `T2` or `T3` run, look for two signs that the slice was too large for one run:
  - the run used more than 80% of its tier's turn budget. Read the turns from `~/.config/opencode/tools/agent-stats.py`. The budget is the tier's `steps` value.
  - the run returned `PARTIAL` because its budget ran out.
  If the slice needs another attempt after such a run, check `divisible` again before you write the next brief. If it is `yes`, classify the failure as `slicing`. Then recut the slice and reset the attempt counter. Do this at the first failure; do not wait for step 8. If it is `no`, classify the failure by step 8 as usual.
- order slices by dependency: schemas, contracts, and interfaces first; callers after; tests and docs last.
- run at most one `code-writer` at a time — slices are sequential by default. Only read-only work parallelizes: the researchers, `finding-verifier`, and the reviewer judging a diff that has stopped moving.
- validate a slice that changes behavior before starting the next one. Never stack unverified slices.
- write the brief per slice, not per feature. A brief whose `changes` list keeps growing is still too big to hand off.
- if you cannot cut clean slices, the task is not ready to delegate: plan it first with `planner` or the `coding-task-planner` skill, or ask the user.

## Work loop

1. Define acceptance criteria and identify material risks. Record `git status --short` as the task baseline; **Working tree at completion** checks the final state against it.
2. Inspect the relevant repository context before researching externally. Before planning anything new, check that it does not already exist. Search by domain concept, not by the wording of the request — the project may call the same thing something else. A targeted grep or read in the main context is enough when you already know the vocabulary; there is no broad-search subagent to delegate this to here. State where you looked. If the behavior already exists, report that instead of building it.
3. When the task is a reported defect, reproduce it before planning a fix. Delegate to `bug-investigator` — reproduction and root cause come back from the same run, so never split them across two delegations or repeat this work at step 7. Run the check yourself only when it is a single command whose output is short; anything noisier belongs inside the subagent. Three outcomes:
   - reproduced — carry the diagnosis, with its severity and confidence, into the plan and the brief;
   - not reproduced — report that to the user and stop; do not fix a defect you could not observe;
   - cannot be reproduced here (missing environment, production data, cluster access) — say so plainly rather than substituting a weaker check, and ask the user how to proceed.
4. Plan non-trivial work. Spawn `planner` only when the approach is genuinely open: several plausible designs, a schema, data, or migration change, or unclear requirements. For a large new work item run the `coding-task-planner` skill instead of `planner`, not before it. Then load the `grilling` skill and let its own test decide: grill the plan when it has open, consequential decisions; when nothing material is open, say so and proceed. Skip it also when the user has already answered the same questions in this session, or asks you to skip.
5. Implement the smallest coherent change: classify it with `code-tier-assessment` and delegate to the tier's `code-writer-t1/t2/t3` agent with the brief above; a trivial slice gets a short brief and goes to `code-writer-t1`. When its report comes back, take a snapshot of the working tree (see **Snapshots**) before anything else touches the tree.
6. Run validation through `test-runner`, forwarding the `Verification` section of the implementer's report as-is — the commands already run with their outcomes, and everything it listed as not verified. `test-runner` closes the unverified gaps and always runs the slice's closing check, even when the implementer already ran it and it passed; apart from that closing check, it must not repeat a check that already passed. The check that closes a slice always runs here, never in the main context: a check nobody recorded is a claim rather than a gate, and a slice whose gate left no row behind cannot be judged afterwards. Exploratory checks — a grep, a single targeted test while still deciding what to do — stay wherever they are cheapest. Never run the project's test suite, build, or lint in the main context: a suite you ran yourself is not a gate (Claude Code enforces this with a hook; here it is on you). Name the slice's `slice_id` in this delegation too: validation is part of the slice's record, and a run without it cannot be attributed to the slice it validated. Around this delegation keep a clean-worktree baseline: before it, record `git status --short` (it lists untracked files too) and the hash `git diff --binary | shasum`; after it, compare both — never pull the diff itself into context. If `test-runner` changed source or repository configuration, block the gate and report the paths — do not auto-revert anything. Any other new path it left, such as a cache or a report, is `junk` for **Working tree at completion**. Pre-existing user changes are baseline and are never reverted.
7. On failure, identify the root cause before fixing. If step 3 already diagnosed this defect, build on that verdict instead of re-running the investigation. Skip `bug-investigator` only when the cause is genuinely one-line and obvious — the fix still goes to a `code-writer`; default to `bug-investigator` otherwise — if you are still deciding whether it counts as obvious, it does not, delegate. Give it the failing command and concise evidence. Never patch just the symptom (suppressing an error, adding a defensive check, retrying) without confirming why the failure actually occurs.
8. From the second failure at the slice's current tier — or the same error twice in a row at that tier — stop editing and run this procedure before anything else:
   1. Record the state: `git status --short` and `git diff --stat`.
   2. Classify the failure. `bug-investigator` returns a root cause only — it never saw the brief and does not know how the work was sliced, so it cannot judge whether the spec is complete or the slice too large. You own the classification; map the diagnosis against your own brief:
      - `executor` — the implementer was not capable enough: raise the tier, attempt spent;
      - `spec` — the brief is incomplete or self-contradictory: return to planning or ask the user, same tier, attempt not spent. This class also applies when each fix makes a new failure appear in a different place: the design is wrong, and a higher tier does not repair a design. If a `planner` plan covers the slice, give the planner the failure report verbatim and name the files that changed on disk since it planned. Continue the same planner only if a resume mechanism exists (see the continuation section below); otherwise spawn a fresh planner with the earlier plan plus the failure report;
      - `env` — flake, broken fixture, missing environment: fix the environment and retry, same tier, attempt not spent;
      - `slicing` — the slice is too large: recut it, same tier, counter reset.
   3. Act on the class. `executor`: keep only the coherent verifiable subset you can name, revert the rest, and raise the tier by one (T1→T2 or T2→T3, never skipping) with a fresh brief plus the handoff block in step 9 — otherwise the next tier builds on the previous one's leftovers. `spec`: return to planning or ask the user. `env`: fix the environment and retry. `slicing`: recut the slice.

   Carry the class into the next brief for that slice as `previous_failure_class`, whichever class it was — the next run is then logged against what it is retrying.

   Raising the tier only cures missing capability. A stronger model or more effort does not repair a contradictory requirement — it invents a confident reading of it.
9. A fresh implementer after a failure gets a fresh brief, never the transcript, plus this handoff block:
   - `previous_tier`: the tier that failed;
   - `previous_failure_class`: the class from step 8;
   - `symptom`: the command and its output, truncated;
   - `tried`: what was changed, with what result;
   - `ruled_out`: hypotheses eliminated, and by what evidence;
   - `diagnosis`: the `bug-investigator` verdict verbatim, with its severity and confidence;
   - `kept`: which edits remain in the working tree.

   A transcript of failed attempts carries the frame the previous implementer was stuck in, not the facts.
10. After validation passes, spawn `reviewer` once, with the same brief, the final diff, the test report, and the snapshot ref of the state under review — not the implementer's or the planner's reasoning. Name in the brief which conditional skills the change calls for (`sql-review` + `sql`, `appsec-review`, `airflow`, `python`); the reviewer loads `code-review` regardless and returns one integrated verdict. Every slice that changed code gets reviewed; you do not get to call a change trivial and skip it. Only the user may waive the review.
11. Triage each review finding without re-deriving it: check it isn't a duplicate or an already-rejected false positive, reject only with evidence, then forward the reviewer's evidence and fix direction verbatim — do not paraphrase or re-diagnose. When a finding is doubted, run `finding-verifier` on it before triaging further. Send it to the implementer that wrote the slice when **Continuation, and its limits** below allows it; otherwise it becomes the brief for a fresh `code-writer`. Rerun affected validation and review afterward. The re-review brief carries the previous findings verbatim plus the delta between the snapshot the previous review judged and the new one (`git diff refs/snapshots/<slice_id>/<prev> refs/snapshots/<slice_id>/<new>`), so `code-review`'s **Re-review** can check those findings first and review only the delta instead of the whole change again.
12. Decide whether the task is complete. A subagent verdict is evidence, not the final decision.

## Snapshots

Implementers never commit, so the work under review exists only in the working tree, and one careless `git stash`, `checkout -- .`, or `reset --hard` loses it. After every implementer run, snapshot the whole working tree into a hidden ref. It makes the loss recoverable whatever command caused it, keeps every iteration, and gives re-review a real delta. The user authorized these snapshots on 2026-09-26 as a standing exception to "commit only when asked": they are commit objects under `refs/snapshots/`, never on a branch, never in `git log`, and a default `git push` does not send them.

Run in the repository root, with `S` set to `<slice_id>/<n>` (`n` counts implementer runs on the slice from 1):

```sh
tmp=$(mktemp -u "${TMPDIR:-/tmp}/snapidx.XXXXXX") && idx=$(git rev-parse --git-path index) && { [ ! -f "$idx" ] || cp "$idx" "$tmp"; } && GIT_INDEX_FILE="$tmp" git add -A && tree=$(GIT_INDEX_FILE="$tmp" git write-tree) && if parent=$(git rev-parse -q --verify HEAD); then sha=$(git commit-tree "$tree" -p "$parent" -m "snapshot $S"); else sha=$(git commit-tree "$tree" -m "snapshot $S"); fi && git update-ref "refs/snapshots/$S" "$sha" && echo "snapshot $S = $sha"; rm -f "$tmp"
```

- It goes through a temporary index, so the tree, the real index, and the branch stay untouched. Untracked files are included and `.gitignore` is respected, so ignored secrets such as `.env` stay out. It works in a worktree and in a repository with no commits. Verified in zsh and bash.
- No `snapshot … = <sha>` line means no snapshot. Stop and report it; never continue the slice on an assumed snapshot.
- You do every revert yourself — step 8, **Unusable and truncated returns** — and only after the run's snapshot exists. A revert restores a recorded state, it is not a code edit, and the snapshot is its undo path, so it needs no confirmation. Changes that `test-runner` made are the exception: report them, never revert them (step 6).
- Restore: `git checkout refs/snapshots/<slice_id>/<n> -- .`. List: `git for-each-ref refs/snapshots/`. The ref keeps the commit alive through `reset --hard`, reflog expiry, and `gc --prune=now`.
- Leave snapshots in place after the task; delete a slice's refs (`git update-ref -d`) only when the user asks.
- Outside a git repository (for example `~/.claude` itself), copy the touched files with `cp` (without `-p`) into `~/.harness-backups/<slice_id>-<n>/` instead, one directory per run, as `CLAUDE.md` requires for every backup.


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
- a security-sensitive surface (authentication, secrets, permissions, untrusted input, data exposure) was reviewed as such — by `reviewer` with `appsec-review` loaded;
- no validation was disabled or weakened merely to pass — `claude-hooks.js`'s config-protection check enforces this for known linter/type-checker configs, but is not a substitute for judgment on everything else;
- `DECISIONS.md` carries any durable decision, constraint, or gotcha the work produced that meets the recording rules of the `DECISIONS.md` section in the global instructions — and nothing that fails it;
- no temporary debugging artifacts remain, and scratchpad files are not referenced from code, docs, or the final answer;
- every path in `git status --short` is accounted for, as **Working tree at completion** describes;
- unverified assumptions and limitations are disclosed.

Do not claim a check passed unless it was executed.

## Working tree at completion

Implementers never commit, and you commit only when the user asks. So the work stays in the working tree, and nothing removes what the task left there unless this step does. Before the final report:

1. Run `git status --short` in every checkout the task touched, each worktree included.
2. Put each path in one class:
   - `deliverable`: part of the result. List it in the final report.
   - `baseline`: in the task baseline from step 1 of **Work loop**. Do not touch it.
   - `junk`: anything else, for example a backup copy (`.bak`, `.orig`), an ad-hoc script, a test cache, a report, or a log.
3. If a tool will make a `junk` path again, extend `.gitignore` for it.
4. List the other `junk` paths in one question to the user. Remove them only after the user says yes.
5. If you cannot put a path in a class, ask the user about it. Do not delete it. Until then the task is not complete.
6. In the final report, list the uncommitted `deliverable` paths and offer one commit. Commit only after the user says yes.
7. For each isolated slice with a merged branch, offer to remove its worktree (`git worktree remove <path>`) and its branch (`git branch -d <branch>`). Ask before each removal. Never remove an unmerged branch or a worktree with uncommitted changes. Name them in the report instead.
8. Snapshot refs stay, as **Snapshots** says. List the task's refs in the report and offer to delete them.

Outside a git repository, compare the touched directories against the backup copy from **Snapshots** instead of `git status`.

## Loop limits

- The same error twice in a row enters the step 8 procedure; it is not a separate action. Widen the analysis there: question earlier assumptions, pull in more context, escalate to `bug-investigator` if not already.
- Attempts per tier: 2 at T1, then 2 at T2, then 2 at T3, then stop and report `PARTIAL` or `BLOCKED`. An attempt is spent only on a failing `test-runner` or `reviewer` verdict — never on the implementer's own account of its work, and never on a `spec`, `env`, or `slicing` classification.
- Hard ceiling of 9 implementer runs on one slice, counting every failure class. Without it the classes that do not spend an attempt loop forever. Keep this count yourself — `subagent-runs-opencode.jsonl` has no notion of a continuation vs. a fresh run, so the log cannot be trusted to hold the tally.
- A failing `reviewer` verdict spends an attempt exactly as a failing `test-runner` verdict does, so the tier ladder above also caps implementation-review cycles; there is no separate cycle limit.
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
6. subagent ledger — only when I ask, or when the task took more than three implementer runs: for every subagent type used in this task, its (model, effort), tokens burned, turns run, and success share (fraction of its own runs that ended in a passing verdict) — read from `~/.claude/logs/subagent-runs-opencode.jsonl` filtered to this task's `slice_id`(s), or `python3 ~/.config/opencode/tools/agent-stats.py` (or `/agent-stats`) for a broader window. Read the log; do not recall the delegation from memory.

## OpenCode-specific notes

What changed from the shared Claude Code skill, and why, so a future edit here or there can tell divergence from drift:

- **Delegation tool**: Claude Code's `Agent` tool → OpenCode's `task` tool, `subagentType` argument.
- **Model names**: `haiku`/`sonnet`/`opus` → this setup's actual models (`dspark/deepseek-v4-flash`, `openrouter/deepseek/deepseek-v4.1-flash-1017`, `openrouter/moonshotai/kimi-k3.1-pro`, `openrouter/google/gemini-4-argon`) — see `~/.config/opencode/agent/*.md` and `opencode.jsonc`.
- **No `Explore`, no `general-purpose`**: OpenCode's agent set here has no broad-search or catch-all subagent. Planning goes to the custom `planner` subagent, which exists in all three harnesses; OpenCode's `plan` is a primary session mode, not something the `task` tool can spawn.
- **`steps` instead of `maxTurns`, no verified continuation**: OpenCode caps agent iterations with the `steps` frontmatter field (`maxSteps` is deprecated); every agent sets it to the same number as its Claude Code `maxTurns` (`reviewer` 120, `code-writer-t1/t2/t3` 30/80/110, `test-runner` 20, and so on). At the cap OpenCode asks the agent to summarize its work instead of cutting it off. Whether a subagent session can be resumed (Claude Code's `SendMessage`/`ListAgents`) is unverified here — see **Continuation, and its limits**.
- **No `first-pass.py`**: not ported. Security review runs through the shared `appsec-review` skill, not Claude Code's built-in `/security-review`; `agent-stats.py` and `context-budget.py` exist at `~/.config/opencode/tools/`.
- **Read-line guardrail, orchestration gate, subagent logging**: all live in the `claude-hooks.js` plugin (`~/.config/opencode/plugins/`), not in separate hook scripts — one file, `tool.execute.before`/`tool.execute.after`.
- **Report contracts**: validated in `claude-hooks.js` (via `report-contracts.mjs`, a JS port of `log-subagent.py`'s `ROLE_CONTRACTS`), checked against the shared golden fixtures by `~/.config/opencode/tools/test-report-contracts.mjs`.
- If you find a new mismatch between this file and how OpenCode actually behaves, fix it here rather than assuming the shared Claude Code text was right — that's the whole reason this override exists.
