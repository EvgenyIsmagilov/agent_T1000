---
name: python
description: python coding standards. use for any work on python code — .py files, packages, scripts, services, airflow pipelines, tests, and python containing embedded sql.
---

# Python

Apply these standards whenever working with Python code.

## Workflow

1. Inspect the project's existing Python version, formatter, linter, type checker, test commands, and local conventions.
2. Follow project configuration when it conflicts with defaults in this skill.
3. Make the smallest focused change that satisfies the task.
4. Run the narrowest relevant checks available in the project.
5. State which checks were run and which were not.

## Code style

- Follow PEP 8, with one deliberate exception: use 2-space indentation, not 4. Never use tabs.
- Keep SQL keywords uppercase.

### Python

- Python 3.11+ unless the project pins another version.
- Type hints are mandatory for all public functions and methods:
  annotate every parameter and the return type (use `-> None` explicitly).
- Use modern typing syntax: `list[str]`, `dict[str, int]`, `X | None` —
  not `List`, `Dict`, `Optional`.
- Avoid `Any`; if unavoidable, leave a short comment why.
- For structured data prefer `dataclass`, `TypedDict`, or pydantic models over raw dicts.
- Code must pass `mypy --strict`, or at minimum be free of obvious type errors when strict checking is not configured or available.

## Validation

- Prefer existing project commands and configuration over invented commands.
- Run focused tests before broader suites.
- Run configured formatting, linting, and type checks when relevant.
- Do not install dependencies, rewrite configuration, weaken checks, or suppress errors merely to make validation pass unless explicitly requested.
- Never claim a check passed unless it was actually executed.
