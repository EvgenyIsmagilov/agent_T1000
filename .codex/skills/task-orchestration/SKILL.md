---
name: task-orchestration
description: orchestrate every task that changes source code or repository configuration with specialized Codex named subagents — tiered implementation, a task brief, planning, test and review gates, failure classification, and a clean-worktree check. do not use for read-only investigation, research, or document-only work.
---

# Task orchestration (Codex)

Codex adaptation of the Claude Code skill of the same name; OpenCode carries a third. The workflow, roles, and gates are the same in all three. What differs here — models, no hooks, no run log, no verified continuation — is marked in place and summarized in **What Codex does not enforce for you** at the end, so a future edit can tell divergence from drift.

Act as the task owner. Keep requirements, decisions, and the final completion decision in the main context. In Codex implementation is delegated too: only a `code-writer` tier edits source or repository configuration — not you, however small the change (see `AGENTS.md`).

## Decide whether to orchestrate

Use this workflow for every task that changes source code or repository configuration, however small; a trivial change goes to `code-writer-t1` with a short brief. Instruction, documentation, and harness configuration files (`AGENTS.md`, skills, agents) are not code for this rule. Do not use it for read-only investigation, research, or document-only work — answer those directly or through a researcher.

## Delegate narrowly

Give every subagent only:

- the exact objective;
- relevant files, commands, versions, and constraints;
- the names of applicable skills to load (never their pasted content);
- required evidence;
- the expected compact output;
- the condition that ends the subtask.

Do not pass the full conversation, unrelated files, or raw historical logs. Ask for conclusions, evidence, and actionable findings.

Use subagents by role (named agents defined in `~/.codex/agents/*.toml`):

- `planner`: read-only implementation plan with slices, risks, and acceptance criteria, for a task whose approach is genuinely open — ahead of writing the brief below;
- `web-researcher`: narrow factual or documentation lookup; if unsure which researcher fits, take this cheaper one;
- `docs-researcher`: ambiguous, version-sensitive, or high-impact research;
- `test-runner`: focused tests, linters, type checks, and build checks;
- `bug-investigator`: root-cause analysis after a non-obvious or repeated failure;
- `code-writer`: focused implementation of a single well-scoped feature slice, fix, or refactor; runs at three tiers (`code-writer-t1/t2/t3`), see **Executor tiers** and the `code-tier-assessment` skill;
- `reviewer`: the single adversarial review gate. It always loads `code-review`, adds `sql-review` and `sql` for meaningful Trino or analytical SQL, `appsec-review` for a security-sensitive surface, and `airflow`/`python` where the change needs them — one run, one verdict;
- `finding-verifier`: verify one specific claim (a review finding, a suspected diagnosis) before spending a `code-writer` run on it — read-only, refutation-first, never hunts for other defects.

There is no `Explore` or `general-purpose` agent here. Do a broad read-only search yourself with `rg` and targeted reads, or delegate to `docs-researcher`/`web-researcher` when it is genuinely a research question rather than a codebase-location question. Do not re-read in the main context what a subagent already summarized.

Run subagents in parallel only when their work is independent and read-only. Never let parallel workers edit the same files.

## Budgets

Every delegation costs tokens and wall-clock, and every report lands back in your context. Spend deliberately.

- Match the role to the question. Each agent file fixes its `model` and `model_reasoning_effort`; whether a spawn can override the model per call is unverified here, so pick the cheaper role instead — `web-researcher` over `docs-researcher`, `code-writer-t1` over `code-writer-t2` when `code-tier-assessment` allows it.
- Know what the heavy roles cost before reaching for one. `reviewer` runs `gpt-6-astra`, the most capable and most expensive model, at `high` effort. Right for a risky diff, absurd for a typo.
- At most 10 subagents in flight at once, and only when each answers a different question. Parallel work multiplies spend immediately and drops several reports into your context at the same moment.
- Actual spend is cut by the `gpt-6-luna` roles at low or medium effort (`web-researcher`, `test-runner`, `code-writer-t1`; `docs-researcher` is on `gpt-6-luna` too, but at `high` effort); reach for `web-researcher` over `docs-researcher` whenever one authoritative source settles the question.
- Never pay twice for the same evidence. A check `code-writer` ran and reported does not get rerun by `test-runner`, and a file a subagent summarized does not get re-read in the main context.
- Codex writes no run log, so cost and report state cannot be measured afterwards. Judge each report by its contract as it arrives: a valid report, a malformed one, and an empty one are different states.

## Executor tiers

`code-writer` ships as three separate agent files, one per tier — `code-writer-t1`, `code-writer-t2`, `code-writer-t3` — because model and effort differ between them.

| Tier | Agent | Model | Effort |
|---|---|---|---|
| T1 | `code-writer-t1` | gpt-6-luna | medium |
| T2 | `code-writer-t2` | gpt-6-sol | medium, the default |
| T3 | `code-writer-t3` | gpt-6-sol | xhigh |

`gpt-6-astra` never writes code — it stays reserved for `reviewer`. Only a `code-writer` tier may edit source or repository configuration: `planner`, `reviewer`, `bug-investigator`, `finding-verifier`, both researchers, and `test-runner` carry `sandbox_mode = "read-only"`, so their boundary is enforced rather than requested.

T2 and T3 work test-first: a failing test, then the smallest code that passes it, one piece of behavior at a time. T1 does not — a red-green cycle on a one-line fix doubles the cost of the cheapest tier for nothing. The rules live in those two agent files; what they must cover comes from the brief's `test_scope`, because an isolated executor cannot ask you mid-run and will stop rather than invent the scope.

Load `code-tier-assessment` before writing the brief and classify the slice against its closed-list criteria; do not eyeball it. Name the tier and, for T1 or T3, the specific box or trigger that put it there, in the brief. A slice with no named trigger for T1 or T3 goes to T2.

Codex keeps no run log, so tier shares cannot be measured here and no target share is set; route each slice by the closed lists and nothing else.

## Reading large files

No hook limits file reads here; hold the same line by hand. Do not pull a large file into context whole when a region, a symbol, or a subagent's summary would answer the question, and when a brief points at a large file, name the region that matters (`file:line` range, symbol, or function).

## Task brief

Before delegating non-trivial work, compose one structured brief and reuse it for every subagent that needs full context — `code-writer` to implement it, `reviewer` to judge the diff against it instead of guessing intent from the code alone. Act like a lead scoping work for an implementer, not writing the code yourself. Include:

- `goal`: the one-sentence outcome.
- `slice_id`: a short stable name for this slice, reused verbatim in every brief about it — retries, escalations, and the fix brief after review. It is what ties the implementer run, its validation and its review together in your briefs and the final ledger; Codex keeps no run log, so without it a slice's outcome cannot be traced afterwards. Claude Code and OpenCode refuse to spawn a `code-writer` tier or `test-runner` without a `slice_id:` line; here nothing refuses, so write it every time.
- `tier`: `T1`, `T2`, or `T3`; for `T1` or `T3`, the box or trigger from `code-tier-assessment` that put it there.
- `context`: why, plus constraints that shape the approach.
- `files_to_inspect`: the specific files to read before changing anything.
- `changes`: per file, a `file` and a one-line `action` — what must change, not how.
- `risks`: concrete ways the change could break something else.
- `test_scope`: for `T2` and `T3`, the public surface the tests must go through, and which of the `risks` above must be covered as failure modes. Both tiers work test-first and will stop rather than guess when the code offers more than one plausible surface, so name it. Breadth is your call, not theirs: happy path plus the named failure modes, not an exhaustive sweep of edge cases. When the project has no test setup, say so here — the implementer will not build one on its own initiative. Omit for `T1`.
- `acceptance_criteria`: how to verify done (build, specific test, observed behavior).
- `attempt` and `run`: on any brief after the first for this slice, which of the two attempts at this tier and which of the nine runs on the slice. Codex keeps no run log, so nothing else holds that count.

Before handing it off:
- If something needed for the brief is missing (unclear goal, unknown files, no acceptance criteria), ask the user directly instead of guessing.
- If you filled gaps by inference, do not proceed silently. Summarize the assumptions against the structure above and get the user to confirm before delegating.

When the work comes from a subagent report — a reviewer finding or a `bug-investigator` diagnosis — do not author a fresh brief and do not summarize. Forward the report's own section verbatim as the brief, adding only the objective and the files in scope. Carry the reporter's qualifiers with it: severity, confidence, and a diagnosis status of `PROBABLE` or `INCONCLUSIVE`. Never present a hedged diagnosis as established, and do not delegate a fix from an `INCONCLUSIVE` or `BLOCKED` report.

### Isolated slices (not used here)

- `worktree`: omit it for an ordinary sequential slice. How Codex isolates a subagent in its own worktree, and which commit such a worktree starts from, is unverified here; do not run slices isolated or commit anything to prepare for isolation until that is established. `code-writer` tiers never commit.

## Slicing multi-file work

`code-writer` takes one area at a time. When the change spans several, cut it into slices yourself before delegating:

- one slice = one coherent change with its own acceptance criteria and no file shared with another in-flight slice. If two slices cannot avoid the same file, they are one slice.
- order slices by dependency: schemas, contracts, and interfaces first; callers after; tests and docs last.
- run at most one `code-writer` at a time — slices are sequential by default. Only read-only work parallelizes: the researchers, `finding-verifier`, and the reviewer judging a diff that has stopped moving.
- validate a slice that changes behavior before starting the next one. Never stack unverified slices.
- write the brief per slice, not per feature. A brief whose `changes` list keeps growing is still too big to hand off.
- if you cannot cut clean slices, the task is not ready to delegate: plan it first with `planner` or the `coding-task-planner` skill, or ask the user.

## Work loop

1. Define acceptance criteria and identify material risks.
2. Inspect the relevant repository context before researching externally. Before planning anything new, check that it does not already exist. Search by domain concept, not by the wording of the request — the project may call the same thing something else. Search with `rg` in the main context; there is no broad-search subagent here. State where you looked. If the behavior already exists, report that instead of building it.
3. When the task is a reported defect, reproduce it before planning a fix. Delegate to `bug-investigator` — reproduction and root cause come back from the same run, so never split them across two delegations or repeat this work at step 7. Run the check yourself only when it is a single command whose output is short; anything noisier belongs inside the subagent. Three outcomes:
   - reproduced — carry the diagnosis, with its severity and confidence, into the plan and the brief;
   - not reproduced — report that to the user and stop; do not fix a defect you could not observe;
   - cannot be reproduced here (missing environment, production data, cluster access) — say so plainly rather than substituting a weaker check, and ask the user how to proceed.
4. Plan non-trivial work. Spawn `planner` only when the approach is genuinely open: several plausible designs, a schema, data, or migration change, or unclear requirements. For a large new work item run the `coding-task-planner` skill instead of `planner`, not before it. Then stress-test it with the user via the `grilling` skill before building. This is the default, not a step reserved for risky plans: the questions are cheapest before any code exists, and a plan that survives them needs no rework later. Skip it only when the user has already answered the same questions in this session, or explicitly asks you to skip.
5. Implement the smallest coherent change: classify it with `code-tier-assessment` and delegate to the tier's `code-writer-t1/t2/t3` agent with the brief above — `code-writer-t1` for trivial work. Never edit source or repository configuration yourself. When its report comes back, take a snapshot of the working tree (see **Snapshots**) before anything else touches the tree.
6. Run validation through `test-runner`, forwarding the `Verification` section of the implementer's report as-is — the commands already run with their outcomes, and everything it listed as not verified. `test-runner` closes the unverified gaps and the canonical check; it must not repeat a check that already passed. The check that closes a slice always runs here, never in the main context: a check nobody recorded is a claim rather than a gate, and the `test-runner` report is the only record of it Codex keeps. Exploratory checks — a grep, a quick sanity run while still deciding what to do — stay wherever they are cheapest; simply do not pre-run the closing check yourself, or `test-runner` is left with nothing to close. Name the slice's `slice_id` in this delegation too: validation is part of the slice's record, and a run without it cannot be attributed to the slice it validated. Around this delegation keep a clean-worktree baseline: before it, record `git status --short` (it lists untracked files too) and the hash `git diff --binary | shasum`; after it, compare both — never pull the diff itself into context. If `test-runner` changed source or repository configuration, block the gate and report the paths — do not auto-revert anything. Pre-existing user changes are baseline and are never reverted.
7. On failure, identify the root cause before fixing. If step 3 already diagnosed this defect, build on that verdict instead of re-running the investigation. When the cause is genuinely one-line and obvious, send the fix straight to a `code-writer` tier without an investigation; default to `bug-investigator` otherwise — if you are still deciding whether it counts as obvious, it does not, delegate. Give it the failing command and concise evidence. Never patch just the symptom (suppressing an error, adding a defensive check, retrying) without confirming why the failure actually occurs.
8. From the second failure on the same slice — or the same error twice in a row — stop editing and run this procedure before anything else:
   1. Record the state: `git status --short` and `git diff --stat`.
   2. Classify the failure. `bug-investigator` returns a root cause only — it never saw the brief and does not know how the work was sliced, so it cannot judge whether the spec is complete or the slice too large. You own the classification; map the diagnosis against your own brief:
      - `executor` — the implementer was not capable enough: raise the tier, attempt spent;
      - `spec` — the brief is incomplete or self-contradictory: return to planning or ask the user, same tier, attempt not spent;
      - `env` — flake, broken fixture, missing environment: fix the environment and retry, same tier, attempt not spent;
      - `slicing` — the slice is too large: recut it, same tier, counter reset.
   3. Act on the class. `executor`: keep only the coherent verifiable subset you can name, revert the rest, and raise the tier by one (T1→T2 or T2→T3, never skipping) with a fresh brief plus the handoff block in step 9 — otherwise the next tier builds on the previous one's leftovers. `spec`: return to planning or ask the user. `env`: fix the environment and retry. `slicing`: recut the slice.

   Carry the class into the next brief for that slice as `previous_failure_class`, whichever class it was — the next brief then records what it is retrying.

   Raising the tier only cures missing capability. A stronger model or more effort does not repair a contradictory requirement — it invents a confident reading of it.
9. A fresh implementer after a failure gets a fresh brief, never the transcript, plus this handoff block:
   - `previous_tier`: the tier that failed;
   - `previous_failure_class`: `executor`;
   - `symptom`: the command and its output, truncated;
   - `tried`: what was changed, with what result;
   - `ruled_out`: hypotheses eliminated, and by what evidence;
   - `diagnosis`: the `bug-investigator` verdict verbatim, with its severity and confidence;
   - `kept`: which edits remain in the working tree.

   A transcript of failed attempts carries the frame the previous implementer was stuck in, not the facts.
10. After validation passes, spawn `reviewer` once for non-trivial or high-risk changes, with the same brief, the final diff, the test report, and the snapshot ref of the state under review — not the implementer's or the planner's reasoning. Name in the brief which conditional skills the change calls for (`sql-review` + `sql`, `appsec-review`, `airflow`, `python`); the reviewer loads `code-review` regardless and returns one integrated verdict.
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
- Restore: `git checkout refs/snapshots/<slice_id>/<n> -- .`. List: `git for-each-ref refs/snapshots/`. The ref keeps the commit alive through `reset --hard`, reflog expiry, and `gc --prune=now`.
- Leave snapshots in place after the task; delete a slice's refs (`git update-ref -d`) only when the user asks.
- Outside a git repository (for example `~/.claude` itself), copy the touched files into the session scratchpad instead, one directory per run.


## Continuation, and its limits

Claude Code can reach a finished subagent again with `SendMessage`, keeping everything it read and wrote in its context. Whether Codex can resume a specific finished subagent rather than spawn a new one is unverified. Until it is, treat every correction as a fresh spawn with the step 9 handoff block (`tried`, `kept`, `ruled_out`), and tell the fresh implementer that the code already on disk is a previous run's work, or it reads the change as someone else's and hesitates to touch it.

If a resume mechanism turns out to exist, use it only under the conditions Claude Code requires: the correction is named (a reviewer finding with `file:line`, a specific failing check, an `env` classification), the slice and tier are unchanged, and the working tree still holds exactly what that agent left. Never continue after an `executor`, `spec`, or `slicing` classification. The stale tree is the hazard: a continued implementer will not re-read files it believes it already knows.

## Completion gate

Finish only when all applicable conditions hold:

- explicit requirements and acceptance criteria are satisfied;
- relevant checks actually passed, and the check that closed the slice ran through `test-runner`, so the slice has a recorded gate rather than a claim;
- no unresolved critical, high, or medium review findings remain;
- the fix addresses the diagnosed root cause, not just the visible symptom;
- Trino or analytical SQL was reviewed for correctness and cluster cost;
- a security-sensitive surface (authentication, secrets, permissions, untrusted input, data exposure) was reviewed as such — by `reviewer` with `appsec-review` loaded;
- no validation was disabled or weakened merely to pass;
- no temporary debugging artifacts remain, and temporary report files are not referenced from code, docs, or the final answer;
- `DECISIONS.md` carries any durable decision, constraint, or gotcha the work produced that passes the reuse test in `AGENTS.md` — and nothing that fails it;
- unverified assumptions and limitations are disclosed.

Do not claim a check passed unless it was executed.

## Loop limits

- The same error twice in a row enters the step 8 procedure; it is not a separate action. Widen the analysis there: question earlier assumptions, pull in more context, escalate to `bug-investigator` if not already.
- Attempts per tier: 2 at T1, then 2 at T2, then 2 at T3, then stop and report `PARTIAL` or `BLOCKED`. An attempt is spent only on a failing `test-runner` or `reviewer` verdict — never on the implementer's own account of its work, and never on a `spec`, `env`, or `slicing` classification.
- Hard ceiling of 9 implementer runs on one slice, counting every failure class. Without it the classes that do not spend an attempt loop forever.
- Keep these counts in the brief itself (`attempt`, `run`, `previous_failure_class`), not in your head: Codex writes no run log, so nothing reconstructs them afterwards. A brief with no count on a slice that has already failed once means you lost the thread — stop and re-read the earlier briefs rather than guess the number.
- Allow at most 3 implementation-review cycles.
- Stop earlier if two consecutive cycles produce no measurable progress.
- Do not rerun a subagent with materially identical input unless new evidence exists.
- When limits are reached, stop speculative edits and report `PARTIAL` or `BLOCKED` with the remaining issue and next concrete action.

## Unusable and truncated returns

Keep three states separate. A valid `PARTIAL` is a legitimate report: it may close a bounded partial step, but its stated remainder still needs work. An empty or unavailable report is unusable. A malformed report (wrong leading status or missing required section) is also unusable, but does not by itself prove the work was interrupted.

Treat a return as actually truncated only with termination evidence such as `stop_reason: max_tokens`, an explicit timeout/error, or another API signal. An unfinished-looking sentence or a bad report format is not proof; at most label it a suspected interruption and keep that uncertainty visible.

Recover in this order:

1. Establish the real state before deciding anything. After `code-writer`, run `git status` and `git diff --stat`: the working tree may hold edits no report ever mentioned. Read-only agents leave nothing behind — only their turns are lost.
2. For empty or unavailable reports, do not close the step. For a malformed report with usable material, ask the same agent to reformat or summarize that material before redoing work; do not automatically reslice, roll back, or repeat the investigation.
3. Inspect writer edits before choosing to keep or revert. Revert to the last clean state only when edits are incoherent or unverifiable. Keep a coherent subset you can name, and treat the remainder as the next slice.
4. For confirmed truncation, re-delegate narrower, never identically. Cut the slice down, list files already changed as done, and state exactly what remains. After a second confirmed truncation on the same slice, question the slicing.
5. Count confirmed truncations and failed recovery attempts against the attempt budget above. Nothing an unusable report claimed to have verified counts as verified.

## Long artifacts

Subagent reports are capped at 450-1,300 words. When the substance genuinely does not fit — a long diff, a full plan, a wide findings table, a captured log — have a subagent that can write put the bulk in a file and return the path plus the summary that does fit. The read-only roles (`reviewer`, `planner`, `bug-investigator`, `finding-verifier`, `test-runner`, both researchers) run under `sandbox_mode = "read-only"` and cannot write files: ask them for a tighter report instead.

- Name the exact path in the brief: a temporary directory outside the project tree, never the project tree itself.
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
6. subagent ledger — only when I ask, or when the task took more than three implementer runs: every subagent role used in this task, with its model and effort, how many runs, and how many ended in a passing verdict — counted from the reports you received and the `attempt`/`run` fields in your briefs, since Codex keeps no run log.

## What Codex does not enforce for you

Claude Code runs these as hooks. Here they are discipline, and nothing stops you skipping them.

- Do not edit a linter, formatter, or type-checker configuration to make a check pass. Fix the code instead. `pyproject.toml`, `setup.cfg`, and `tox.ini` mix tool settings with dependencies — a dependency change there is ordinary, a widened `ignore` list is not. If changing the config is genuinely the task, say so out loud rather than slipping it into a fix.
- Do not pull a large file into context whole when a region, a symbol, or a subagent's summary would answer the question.
- Delegating is a decision, not a reflex. Load this skill before the first spawn, then pick the role and the tier deliberately and write the brief. The exceptions match the Claude Code and OpenCode gates: the two researchers, and a lone `reviewer` for a review such as `/code-review`.
- There is no run log: counts live in the briefs, and the ledger is built from the reports.
- Nothing refuses a brief without `slice_id`, and agents have no turn cap: write `slice_id` every time, and treat a run that keeps going without progress as a failure to classify, not as work in progress.

## Pilot

When evaluating this workflow, read [the pilot protocol](references/pilot.md). Use isolated worktrees and never apply pilot changes to the user's active checkout.
