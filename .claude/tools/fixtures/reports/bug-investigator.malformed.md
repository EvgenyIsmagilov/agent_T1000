Let me walk through what I found.

### Result
CONFIRMED — the parser crashes on an empty token because of a missing null check.

### Root cause
`parse_token()` dereferences `token.value` before checking `token` is not `None`.

### Evidence
- `src/parser.py:42` — `token.value` accessed with no prior `None` check.

### Fix direction
Add a guard in `parse_token()`.

### Verification
`pytest tests/test_parser.py -k empty_token` should pass once the guard is added.
