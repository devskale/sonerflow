# Agent Notes (ghsorter)

## Tooling Policy

- Always and only use uv for Python environment management, dependency installation, and running commands.
- Do not use pip, poetry, pipenv, conda, or system Python directly for this project.

## Common Commands

```bash
uv sync
uv run credgoo --setup
uv run ghsorter doctor
uv run ghsorter ingest
uv run ghsorter analyze --interactive-steering
uv run ghsorter export
```

## Dependency Management

```bash
uv add <package>
uv remove <package>
uv lock
```
