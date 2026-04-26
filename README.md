# ghsorter (CLI MVP)

Turns your GitHub stars into steerable “areas of interest” and exports them as structured files.

## Prereqs

- GitHub CLI authenticated: `gh auth login`
- uv installed

## Commands

```bash
uv sync
uv run ghsorter doctor
uv run ghsorter ingest
uv run ghsorter analyze --interactive-steering
uv run ghsorter export
uv run ghsorter --dosort
uv run ghsorter --dosort --full
uv run ghsorter --dosort --pause
uv run ghsorter --dosort --live
uv run ghsorter cluster --method hdbscan --top-level 20
uv run ghsorter cluster --method kmeans --k 200 --top-level 20
uv run ghsorter cluster --seed-file ./seed_lists.json --top-level 25
```

## Storage

Defaults to `./.ghsorter_store/` if `./.ghsorter.json` exists (override via `--store <path>` or `GHSORTER_HOME`). Otherwise defaults to `~/.ghsorter/`.

## LLM config (OpenAI-compatible)

## Source data

`ghsorter ingest` stores a curated, high-signal subset of repo metadata from `/user/starred` in `catalog.json` (not the full raw payload).

Default LLM settings:

- base URL + model come from `./.ghsorter.json` (project) or `~/.ghsorter/` (global), or env vars
- context window is model-dependent; pass `--context-tokens` if needed

Optional overrides (env vars):

```bash
export OPENAI_BASE_URL="https://api.openai.com/v1"
export OPENAI_MODEL="openai@gpt-4o-mini"
```

Then run:

```bash
uv run ghsorter analyze
```

## Model context windows

Different models have different context windows (input + output). If your model has e.g. 16384 tokens total, run:

```bash
uv run ghsorter analyze --model 'tu@qwen-coder-30b' --context-tokens 16384
```

## Persist defaults (config)

To store default model + context window into `./.ghsorter_store/config.json` (or your chosen `--store`):

```bash
uv run ghsorter config set --model 'tu@qwen-coder-30b' --context-tokens 16384
```

To store defaults in a project-local config file (`./.ghsorter.json`):

```bash
uv run ghsorter config set --project --base-url 'https://your-openai-compatible.example/v1' --model 'tu@qwen-coder-30b' --context-tokens 16384
```
