---
name: git
description: git and pull/merge request conventions. use when committing, branching, rebasing, pushing, resolving conflicts, or opening pull requests (github) and merge requests (gitlab).
---

# Git

Apply these standards for any git, PR, or MR work. The repository's existing conventions — message language, branch naming, merge style — win over defaults here.

## Workflow

1. Inspect before acting: `git status`, the current branch and its upstream, and `git log --oneline -15` for the repo's message style and language.
2. Stage explicitly by path after reviewing `git status` and the diff; broad `git add -A` silently picks up junk and secrets.
3. When untracked output contains artifacts (build output, caches, `.DS_Store`, env files), extend `.gitignore` instead of committing them.
4. In LFS-enabled repos, verify new large or binary files match an LFS pattern (`git lfs track`) before their first commit — a raw binary in history is effectively permanent.

## Commits

- One logical change per commit; split unrelated edits.
- Subject up to ~72 chars stating what changed and why it matters. Add a body when the why is not obvious from the diff.
- Write messages in the language of the repo's recent history; default to Russian in new personal repos.
- Each message must distinguish its commit from its neighbors: a run of identical or near-identical messages means the split or the wording is wrong.
- Make pre-commit hooks pass by fixing the cause; `--no-verify` only with explicit approval.

## Branches

- Branch for anything that becomes a PR/MR, is risky, or is experimental; committing straight to the default branch is fine only where the repo already works that way.
- Name branches lowercase kebab-case with an intent prefix: `feat/...`, `fix/...`, `chore/...`.
- Rebase only commits that exist nowhere but your branch; after any agreed history rewrite push with `--force-with-lease`, never bare `--force`.
- When resolving conflicts, understand the intent of both sides, then rerun the affected checks.

## Pull and merge requests

- GitHub: use `gh`. GitLab: use `glab` when available; otherwise prepare the title and description and hand them over for manual creation.
- One PR/MR = one coherent change. The title follows commit-subject rules.
- Description: what changed, why, how it was verified (commands and results), known risks or limitations. State plainly what was not tested.
- Confirm the target branch is the intended one before creating.
- Merge only with green CI and the approvals the repo requires.

## Validation

- After committing: `git status` is clean and `git show --stat` matches the intent.
- After pushing or opening a PR/MR: verify the branch is on the remote and the PR/MR actually exists (URL).
- Never claim committed, pushed, or merged unless it was actually verified.
