# Contributing

## Dev setup

```
pip install -e ".[dev]"
```

## Before opening a pull request

Run both of these and fix anything they flag:

```
ruff check .
pytest
```

## Guidelines

- Keep user-facing messages short and friendly — they're read by someone waiting for a recording to
  finish, not debugging a stack trace.
- Never commit anything from `data/` (video lists, history, passwords, recordings are all local and
  git-ignored on purpose).
- Use conventional commit messages (e.g. `fix: ...`, `feat: ...`, `docs: ...`).
