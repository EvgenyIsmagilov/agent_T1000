### Result
CONFIRMED — the parser crashes on an empty token because of a missing null check.

### Root cause
`parse_token()` dereferences `token.value` before checking `token` is not `None`.

### Evidence
- `src/parser.py:42` — `token.value` accessed with no prior `None` check.
- Ran `python3 -c "from parser import parse_token; parse_token(None)"` — raised `AttributeError`.

### Reproduction
`python3 -c "from parser import parse_token; parse_token(None)"` reliably reproduces the crash.

### Fix direction
Add a guard in `parse_token()` at `src/parser.py:42` returning early when `token is None`.

### Verification
`pytest tests/test_parser.py -k empty_token` should pass once the guard is added.
