# Review comments

Read this file before you post any comment on a PR/MR. Post findings as inline comments on the changed line.

## Rules

1. Show the user the list of comments and wait for approval. Other people can see a posted comment.
2. Record the head SHA when you read the diff. Before you post, check that the head SHA did not change. If it changed, read the diff again.
3. Make one comment for one finding: one bug, one remark, or one question. Do not collect findings in one comment or in a summary comment. The review `body` states the verdict only. It lists no findings.
4. Start each comment with a label: `blocking`, `nit`, or `question`. Map severity: CRITICAL, HIGH, and MEDIUM to `blocking`. Map LOW to `nit`.
5. Put each comment on the changed line. Use a general comment only for the whole PR/MR (scope, description, verdict). Use it also when the line is not in the diff.
6. Before you post, read the open threads. If a thread holds the same finding, reply in that thread. Do not open a duplicate.
7. Do not resolve a thread that you did not open.
8. For a small and certain fix, add a suggestion block. If the fix is not certain, do not use a suggestion block.
9. Submit the review as `COMMENT`. Use `APPROVE` or `REQUEST_CHANGES` only with explicit user approval.

## GitHub

Use one review for all comments. Write the request body to a JSON file in the scratchpad. Send it with `--input`.

```
gh api repos/{owner}/{repo}/pulls/<n>/reviews --input review.json
```

`gh` fills in `{owner}` and `{repo}` itself. Replace `<n>` and the other `<...>` values by hand.

```json
{
  "commit_id": "<head sha>",
  "event": "COMMENT",
  "body": "<one-line verdict>",
  "comments": [
    {"path": "src/a.py", "line": 12, "side": "RIGHT", "body": "blocking: <finding>"}
  ]
}
```

- Always set `event`. If you omit it, the review stays `PENDING` and nobody sees it. `body` is required for `COMMENT`.
- For a removed line, set `"side": "LEFT"`.
- For a range, add `start_line` and `start_side`.
- Each `line` must be inside a diff hunk at `commit_id`. Check this before you send.
- A 422 response can reject the whole review. If it does, remove or fix the bad comments, then send the review again. Check with the read-back that no partial review exists.
- For a suggestion, put a block ```` ```suggestion ```` in the comment `body`.
- To reply in a thread, use `gh api repos/{owner}/{repo}/pulls/<n>/comments/<comment-id>/replies -f body=...`.
- After you post, read the review back and check that its state is not `PENDING`.

## GitLab

The glab note commands are experimental. The commands below match the help text of glab 1.120.0. The online docs show a `glab mr note draft` group with `draft create` and `draft publish` instead. Run `glab mr note --help` and use the group that your glab version shows.

Get user approval first (rule 1). Every command below stays private until step 4.

1. Check for existing drafts: `glab api projects/:id/merge_requests/<iid>/draft_notes`. `publish` sends all your own pending comments. This includes drafts that you or the user started in the web UI. If drafts exist, stop and tell the user.
2. For each comment, run `glab mr note create <iid> --draft --file <path> --line <n> -m "<text>"`. For a range, use `--line <start>:<end>`. For a removed line, use `--old-line <n>` instead of `--line`. `--old-line` takes one line only.
3. To reply in a thread, run `glab mr note create <iid> --draft --reply <discussion-id-prefix> -m "<text>"`. Get the ids with `glab mr note list <iid> -F json`.
4. Run `glab mr note publish <iid> --yes`.

`glab` fills in `:id` itself. Replace `<iid>` and the other `<...>` values by hand.

### API fallback

Use the API if a flag is missing, or for a range that mixes removed and added lines. A discussion sent this way is visible at once, so get user approval for each one.

```
glab api projects/:id/merge_requests/<iid>/discussions --method POST --input discussion.json
```

```json
{
  "body": "blocking: <finding>",
  "position": {
    "position_type": "text",
    "base_sha": "<from diff_refs>",
    "start_sha": "<from diff_refs>",
    "head_sha": "<from diff_refs>",
    "old_path": "src/a.py",
    "new_path": "src/a.py",
    "new_line": 12
  }
}
```

- Get the three SHA values from `diff_refs` in `glab api projects/:id/merge_requests/<iid>`. If `diff_refs` is empty, use the first entry of `.../merge_requests/<iid>/versions` (`base_commit_sha`, `start_commit_sha`, `head_commit_sha`).
- For an unchanged (context) line, set both `old_line` and `new_line`. For a removed line, set only `old_line`.
- For a range, add `line_range` with `start` and `end`. Each holds `line_code` and `type`. Set `type` to `new` for an added line and to `old` otherwise. The format of `line_code` is `<sha1 of file path>_<old line>_<new line>`.
- If the call fails, report the error and stop. Do not try other values on a live MR.

### Suggestions

Put a block ```` ```suggestion:-N+M ```` in the comment. Set N lines above and M lines below the commented line. For a range, cover all its lines. The maximum is 100 lines above and 100 lines below.

## Not verified

- Checked in the GitLab docs: the required `position` fields, the line rules, the `line_code` format, the suggestion limits, and the draft-note endpoints.
- Not documented: a JSON body with a nested `position`, the error for a wrong position, the `line_code` computation, and the `line_range` fields of a draft note. Do a dry run on a scratch MR with the owner present before first use.
- Not tested against a live repository: the 422 behavior of GitHub and the draft flow of glab.
