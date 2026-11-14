# Project review criteria

Edit this file to match the project. The reviewer reads it for every pull request.

## Report
- Correctness bugs: wrong logic, off-by-one errors, unhandled errors, race conditions, resource leaks.
- Security problems: injection, missing authentication or authorization checks, secrets in code, unsafe handling of untrusted input.
- Changed behavior without a test that covers it.
- Breaking changes to public contracts: API shapes, CLI flags, file formats, database schemas, exit codes.
- Data loss: deletes or overwrites that cannot be undone.

## Do not report
- Style, formatting, naming preferences, and import order.
- Missing comments or documentation.
- Refactoring ideas that do not fix a defect.
- Problems in code that the diff does not change, unless the change makes that code wrong: a caller that now breaks, a contract that no longer holds, or a test that no longer matches.

## Project notes
- Python code uses 2-space indentation. This is intended. Do not report it.
