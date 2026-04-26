# Product Requirements Document (PRD): ghsorter (CLI MVP)

## 1. Problem

GitHub “Stars” becomes unusable after hundreds/thousands of starred repositories. Users want to turn an unstructured pile of starred repos into named, searchable “areas of interest” (collections) with minimal manual effort.

## 2. Goals

- Fetch a user’s starred repos reliably (1000+).
- Persist a local catalog that can be incrementally refreshed.
- Analyze the catalog and propose “areas of interest” (collections).
- Produce structured outputs (files) that can be searched, reviewed, and edited.
- Support an iterative workflow: re-run analysis as the user refines categories.
- Provide an extensible architecture so the same workflow can later work for other sources (e.g., Pocket, YouTube, arXiv, browser bookmarks).

## 3. Non-Goals (for CLI MVP)

- A full web UI (considered later).
- Perfect categorization on first run.
- Team/collaboration features.
- Guaranteed round-trip sync back into GitHub (GitHub “star lists” support is optional and only if stable APIs exist).

## 4. Target Users

- Individual developers with large GitHub star collections (500–10,000).
- People who want lightweight “personal knowledge management” for repos.

## 5. Core Concept

The product is a pipeline:

1. **Ingest** items from a source (GitHub stars).
2. **Store** them in a local, versionable structure (JSON).
3. **Enrich** each item with metadata (language, topics, description, last push, etc).
4. **Classify** items into “areas of interest” (collections) using AI suggestions + user confirmation.
5. **Export** outputs (JSON + optional Markdown) for browsing and reuse.

This pipeline is designed as a reusable pattern with pluggable “sources” and “classifiers”.

## 6. MVP Workflow (CLI)

1. User authenticates via GitHub CLI (existing `gh auth login`).
2. User runs `ghsorter ingest` to fetch starred repos and persist them locally.
3. User runs `ghsorter analyze` to:
   - propose interest areas
   - incorporate user “steering” inputs to shape interest areas
   - assign repos to one or more areas
   - write results to `output/` (structured files)
4. User optionally runs `ghsorter review` to interactively accept/edit category assignments in the terminal.
5. User re-runs `analyze` as needed; edits should be preserved.

## 7. Functional Requirements

### 7.1 Ingestion

- Use GitHub CLI authentication.
- Fetch the complete starred repo list for the authenticated user.
- Support pagination and rate limits gracefully.
- Store raw source payload (for audit/debug) and normalized records.
- Support incremental refresh:
  - add new stars
  - update metadata for existing repos
  - mark repos that were unstarred (optional)

### 7.2 Local Storage (JSON-first)

- Store all data under a single project directory (default: `~/.ghsorter/`).
- Maintain:
  - `catalog.json` (normalized repo records)
  - `labels.json` (user-defined categories/areas)
  - `assignments.json` (repo → categories mapping)
  - `steering.json` (optional user intent, constraints, and seeds used by analyze)
  - `runs/` (optional run artifacts)
- Be resilient to manual edits: validate and repair where possible.

### 7.3 Analysis / Categorization

- Provide AI-assisted categorization using a “bring-your-own key” approach.
- Prefer an OpenAI-compatible API interface so the user can configure:
  - `base_url`
  - `model` (plus optional fallbacks)
  - `api_key`
- Allow the user to steer the creation of interest areas (categories) at analysis time:
  - provide a high-level intent (e.g., “help me find repos I might actually use for backend engineering”)
  - provide seed categories to start from (names + optional descriptions)
  - provide constraints (target number of categories, naming style, allowed overlaps)
  - provide “positive” and “negative” examples (repos that must/must-not belong together)
  - lock/pin categories so they persist across re-runs
- Input features available to the classifier:
  - repo name, owner, description
  - topics
  - primary language
  - README (optional, configurable)
  - stats (stars, forks)
  - timestamps (created, pushed, updated)
- Output requirements:
  - categories (“areas of interest”) with a stable identifier + display name
  - per-repo assignment to 0..N categories
  - confidence score + rationale (short text) to support review
- Must support user overrides:
  - user can rename categories
  - user can reassign repos
  - user can pin/lock assignments so future runs don’t overwrite them

### 7.4 Export

- Always produce a structured, machine-readable export (JSON).
- Optionally produce a human-readable export (Markdown) grouped by category.
- Exports must include enough metadata to be used by other tools.

### 7.5 Review / Editing (Terminal)

- Provide an interactive review mode for:
  - approving suggested categories
  - resolving “uncategorized” repos
  - splitting/merging categories
- Editing must update the persisted JSON artifacts.

## 8. CLI Surface (Draft)

- `ghsorter ingest [--since <date>] [--output-dir <path>]`
- `ghsorter analyze [--base-url <url>] [--model <name>] [--model-fallback <name>...] [--readme] [--steer <file>] [--seed-areas <file>] [--target-areas <n>] [--interactive-steering]`
- `ghsorter review`
- `ghsorter export [--format json|md|both] [--output-dir <path>]`
- `ghsorter doctor` (validate local store + auth)

## 9. Data Model (Draft)

### 9.1 Repo Record

- `id`: stable id (GitHub numeric id if available)
- `full_name`: `owner/name`
- `html_url`
- `description`
- `topics`: string[]
- `language`
- `starred_at` (if available)
- `pushed_at`, `updated_at`, `created_at`
- `stats`: `{ stargazers_count, forks_count, open_issues_count }`
- `source`: `{ type: "github", fetched_at, raw_ref }`

### 9.2 Category (Area of Interest)

- `id`: stable slug/uuid
- `name`: display name (e.g., “Observability”)
- `description` (optional)
- `created_at`, `updated_at`

### 9.3 Assignment

- `repo_id`
- `category_ids`: string[]
- `locked`: boolean
- `confidence`: number (0..1, optional if locked)
- `rationale`: string (optional)

### 9.4 Steering (User Intent + Constraints)

The analyze command can accept a steering file (e.g., `steering.json`) that shapes how interest areas are created and how repos get assigned.

#### Steering File Schema (Draft)

- `version`: string (e.g., `"1"`)
- `intent`: string (high-level goal for categorization)
- `constraints`:
  - `target_area_count`: number (soft target; can be a range later)
  - `allow_multi_label`: boolean (repo can belong to multiple areas)
  - `max_areas_per_repo`: number (optional)
  - `naming_style`: string (e.g., `"concise"`, `"descriptive"`)
  - `overlap_policy`: string (e.g., `"allow"`, `"minimize"`)
- `seed_areas`: array of:
  - `name`: string
  - `description`: string (optional)
  - `locked`: boolean (optional; prevents deletion/renaming by analysis)
- `examples`:
  - `together`: array of groups, each group:
    - `repo_full_names`: string[] (e.g., `["owner/repo", "owner2/repo2"]`)
    - `note`: string (optional)
  - `separate`: array of groups, each group:
    - `repo_full_names`: string[]
    - `note`: string (optional)
- `locked_area_ids`: string[] (optional; preserve these area IDs across re-runs)
- `notes`: string (optional; freeform)

#### Example `steering.json`

```json
{
  "version": "1",
  "intent": "Create practical buckets I can actually use for backend + infra work. Prefer tools/frameworks over tutorials.",
  "constraints": {
    "target_area_count": 20,
    "allow_multi_label": true,
    "max_areas_per_repo": 3,
    "naming_style": "concise",
    "overlap_policy": "minimize"
  },
  "seed_areas": [
    { "name": "Observability", "description": "Tracing, metrics, logging, APM", "locked": true },
    { "name": "Databases", "description": "SQL/NoSQL engines, tooling, ORMs" },
    { "name": "DevOps", "description": "CI/CD, containers, IaC" }
  ],
  "examples": {
    "together": [
      {
        "repo_full_names": ["open-telemetry/opentelemetry-collector", "grafana/grafana"],
        "note": "Same area: observability stack"
      }
    ],
    "separate": [
      {
        "repo_full_names": ["tensorflow/tensorflow", "hashicorp/terraform"],
        "note": "Do not mix ML and infra tooling"
      }
    ]
  },
  "notes": "Prefer stable, maintained projects over archived repos."
}
```

## 10. Extensibility Requirements

- Sources are pluggable: `Source` interface for ingest + normalization.
- Classifiers are pluggable: rule-based baseline and LLM-based classifier share a common output contract.
- Storage layer abstracts JSON now, but can swap to SQLite later without changing command behavior.

## 11. Security / Privacy

- No tokens stored in files by default; reuse GitHub CLI auth.
- LLM keys provided via environment variables only.
- Avoid uploading README content to LLM by default; require explicit `--readme`.
- Allow a `--redact` mode to remove owner names/URLs from LLM prompts (reduces quality but increases privacy).

## 12. Success Criteria

- Ingest completes for 1000+ stars without manual intervention.
- First-pass analysis yields a manageable number of categories (e.g., 10–50) with low manual correction effort.
- Re-running ingest/analyze does not destroy user edits (locked assignments preserved).
- Exports are useful standalone artifacts.

## 13. Open Questions

- Preferred programming language/runtime for the CLI (defer decision; keep architecture/runtime-agnostic).
- GitHub “star lists” write-back should be a post-MVP step once local workflow is proven.
