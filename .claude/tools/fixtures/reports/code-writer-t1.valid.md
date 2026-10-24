### Result
DONE — added the missing null check and covered it with a test.

### Changes
- `src/parser.py:42` — added a null guard before dereferencing `token`, why: root cause of the crash.
- `tests/test_parser.py:88` — new regression test for the empty-token case.

### Verification
- `pytest tests/test_parser.py -k empty_token` — passed, 1 test.
- Not verified: the full parser suite (out of scope for this slice).
