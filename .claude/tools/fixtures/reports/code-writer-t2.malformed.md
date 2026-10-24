I made the following change to fix the bug.

### Result
DONE — added the missing null check and covered it with a test.

### Changes
- `src/parser.py:42` — added a null guard.

### Verification
- `pytest tests/test_parser.py -k empty_token` — passed, 1 test.
