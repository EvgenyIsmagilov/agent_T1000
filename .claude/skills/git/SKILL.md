---
name: git
description: git and pull/merge request conventions. use when committing, branching, rebasing, pushing, resolving conflicts, opening, reviewing, commenting on, or merging pull requests (github) and merge requests (gitlab).
---

# Git

Apply these standards for any git, PR, or MR work. The repository's existing conventions win over defaults here: message language, branch naming, merge style.

## Workflow

1. Run `git status`. Find the current branch and its upstream.
2. Read `git log --oneline -15`. Learn the repo's message style and language.
3. Review the diff. Then stage by explicit path. A broad `git add -A` picks up junk and secrets.
4. If untracked output contains artifacts (build output, caches, `.DS_Store`, env files), extend `.gitignore`. Do not commit them.
5. In an LFS repo, check that each new large or binary file matches an LFS pattern (`git lfs track`) before you commit it. A raw binary in history is effectively permanent.
6. If a secret is in a commit that is not pushed, rewrite that commit without the secret. If the commit is pushed, stop and tell the user to rotate the secret.

## Commits

- Make one logical change per commit. Split unrelated edits.
- Write a subject of about 72 characters or fewer that states what changed. Add a body when the reason is not clear from the diff.
- Write messages in the language of the repo's recent history. If the repo has no history, write in English.
- Make each message different from its neighbors. A run of near-identical messages means the split or the wording is wrong.
- If a pre-commit or commit-msg hook fails, fix the cause. Do not use `--no-verify` without explicit user approval.
- After a failed hook, run `git log -1` to see whether the commit exists. If it does not, stage again and make a new commit. Do not use `--amend`, because it changes the previous commit.

## Branches

- Make a branch for anything that becomes a PR/MR, is risky, or is experimental. Commit to the default branch only if the repo already works that way.
- Name branches in lowercase kebab-case with an intent prefix: `feat/...`, `fix/...`, `chore/...`.
- Rebase only commits that exist on your branch alone.
- After an agreed history rewrite, push with `--force-with-lease`. Never use bare `--force`.
- When you resolve a conflict, find the intent of both sides. Then run the affected checks again.

## Pull and merge requests

- GitHub: use `gh`. GitLab: use `glab` when available. Otherwise prepare the title and description for manual creation.
- Before you use `gh` or `glab`, run `gh auth status` or `glab auth status`. If the check fails, tell the user.
- Make one PR/MR for one coherent change.
- Write the title by the commit-subject rules. A reviewer must understand the change from the title alone.
- Open the description with this template, filled in. If the repo has its own PR template, put these lines above it. Write `—` for a role that was not used.
  ```
  * модель планировщик:
  * модель разработчик:
  * модель ревьюер:
  * что сделано:
  ```
  After the template, give the reason, the verification (commands and results), and known risks or limitations. State plainly what you did not test.
- Confirm that the target branch is the intended one before you create the PR/MR.
- To review a PR/MR or to post comments, read `references/review-comments.md` first.
- Before you push, open a PR/MR, or merge, get user approval. Other people can see these actions.
- Merge only with green CI and the reviews that the repo rules require. These reviews are not user approval.

## Validation

- After you commit, check that `git status` is clean and `git show --stat` matches the intent.
- After you push or open a PR/MR, check that the branch is on the remote. Check that the PR/MR exists (URL).
- Never claim committed, pushed, or merged unless you verified it.
