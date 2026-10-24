# Orchestration pilot protocol

Use 10 completed, representative coding tasks in isolated worktrees: three small changes, three multi-file changes, two debugging tasks, one public-contract task, and one regression-risk task.

For each task, record the accepted outcome, confirmed review findings, elapsed time, observable model-usage data when available, test commands and exit codes, and any worktree changes made by `test-runner`.

Compare the candidate workflow to the previous rules. Promote it only if all pilot tasks pass their test and review gates with no source or repository-configuration edits by `test-runner`, the accepted-result rate is not lower, and median time or observed usage is no more than 20% worse. Otherwise, retain the previous rules and revise only the failing policy.
