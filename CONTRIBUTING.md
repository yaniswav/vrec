# Contributing

## Dev setup

```
pip install -e ".[dev]"
pre-commit install
```

`pre-commit install` sets up a git hook that runs the formatting/lint checks (and a few basic file
hygiene checks) automatically on `git commit`. You can also run it on demand for all files with
`pre-commit run --all-files`.

## Before opening a pull request

Run all of these and fix anything they flag:

```
ruff check .
ruff format --check .
mypy
pytest
```

`node .github/scripts/check-js.js` also runs in CI, to catch a syntax error in `src/vrec/js/*.js`
(each file must be a single JS expression, see the comment at the top of that script).

To check test coverage locally:

```
pytest --cov --cov-report=term
```

## Guidelines

- Keep user-facing messages short and friendly: they're read by someone waiting for a recording to
  finish, not debugging a stack trace.
- Never commit anything from `data/` (video lists, history, passwords, recordings are all local and
  git-ignored on purpose).
- Use conventional commit messages (e.g. `fix: ...`, `feat: ...`, `docs: ...`).
- Every new optional behavior ships with a feature toggle (`src/vrec/features.py`) and is documented,
  so it can be disabled without a code change if it misbehaves.
