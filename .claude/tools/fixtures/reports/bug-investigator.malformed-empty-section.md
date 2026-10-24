### Result
CONFIRMED — the parser crashes on an empty token because of a missing null check.

### Root cause
`parse_token()` dereferences `token.value` before checking `token` is not `None`.

### Evidence

### Fix direction
Add a guard in `parse_token()`.

### Verification
`pytest tests/test_parser.py -k empty_token` should pass once the guard is added.
