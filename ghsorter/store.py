from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .util import AppPaths, read_json, write_json_atomic


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(s: Any) -> datetime | None:
    if not isinstance(s, str) or not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _signals(repo: dict[str, Any]) -> dict[str, Any]:
    stars = repo.get("stargazers_count")
    stars = int(stars) if isinstance(stars, int) else None
    created = _parse_dt(repo.get("created_at"))
    last_activity_at = repo.get("pushed_at") or repo.get("updated_at")
    last_activity = _parse_dt(last_activity_at)
    now = datetime.now(timezone.utc)

    age_days = (now - created).days if created else None
    recency_days = (now - last_activity).days if last_activity else None

    stars_per_year = None
    stars_per_month = None
    if stars is not None and age_days is not None and age_days > 0:
        years = age_days / 365.0
        months = age_days / 30.0
        stars_per_year = stars / years if years > 0 else None
        stars_per_month = stars / months if months > 0 else None

    return {
        "stars": stars,
        "last_activity_at": last_activity_at,
        "age_days": age_days,
        "recency_days": recency_days,
        "stars_per_year": stars_per_year,
        "stars_per_month": stars_per_month,
    }


def load_config_file(path: Path) -> dict[str, Any]:
    raw = read_json(path.expanduser())
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    raise ValueError(f"Invalid config format: {path}")


def save_config_file(path: Path, config: dict[str, Any]) -> None:
    write_json_atomic(path.expanduser(), config)


def load_config(paths: AppPaths) -> dict[str, Any]:
    raw = read_json(paths.config_path)
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    raise ValueError(f"Invalid config format: {paths.config_path}")


def save_config(paths: AppPaths, config: dict[str, Any]) -> None:
    write_json_atomic(paths.config_path, config)


def normalize_repo(repo: dict[str, Any], fetched_at: str) -> dict[str, Any]:
    owner = repo.get("owner") if isinstance(repo.get("owner"), dict) else {}
    license_obj = repo.get("license") if isinstance(repo.get("license"), dict) else {}
    topics = repo.get("topics")
    if not isinstance(topics, list):
        topics = []
    topics = [t for t in topics if isinstance(t, str) and t]

    stats = {
        "stargazers_count": repo.get("stargazers_count"),
        "forks_count": repo.get("forks_count"),
        "watchers_count": repo.get("watchers_count"),
        "open_issues_count": repo.get("open_issues_count"),
    }
    return {
        "id": repo.get("id"),
        "full_name": repo.get("full_name"),
        "html_url": repo.get("html_url"),
        "description": repo.get("description"),
        "topics": topics,
        "language": repo.get("language"),
        "owner": {"login": owner.get("login"), "type": owner.get("type")},
        "license": {"spdx_id": license_obj.get("spdx_id"), "name": license_obj.get("name")},
        "visibility": repo.get("visibility"),
        "private": bool(repo.get("private") or False),
        "default_branch": repo.get("default_branch"),
        "homepage": repo.get("homepage"),
        "is_template": bool(repo.get("is_template") or False),
        "starred_at": repo.get("starred_at"),
        "pushed_at": repo.get("pushed_at"),
        "updated_at": repo.get("updated_at"),
        "created_at": repo.get("created_at"),
        "stats": stats,
        "signals": _signals(repo),
        "archived": bool(repo.get("archived") or False),
        "fork": bool(repo.get("fork") or False),
        "source": {"type": "github", "fetched_at": fetched_at},
    }


@dataclass(frozen=True)
class Catalog:
    repos: list[dict[str, Any]]
    meta: dict[str, Any]


def load_catalog(paths: AppPaths) -> Catalog:
    raw = read_json(paths.catalog_path)
    if not raw:
        return Catalog(repos=[], meta={})
    if isinstance(raw, dict):
        repos = raw.get("repos") or []
        meta = raw.get("meta") or {}
        if isinstance(repos, list) and isinstance(meta, dict):
            return Catalog(repos=repos, meta=meta)
    raise ValueError(f"Invalid catalog format: {paths.catalog_path}")


def save_catalog(paths: AppPaths, repos: list[dict[str, Any]], *, source: str, fetched_at: str | None = None) -> None:
    meta: dict[str, Any] = {"updated_at": _now_iso(), "source": source, "schema_version": "3"}
    if fetched_at:
        meta["fetched_at"] = fetched_at
    payload = {"meta": meta, "repos": repos}
    write_json_atomic(paths.catalog_path, payload)


def load_steering(path: Path) -> dict[str, Any] | None:
    raw = read_json(path)
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError(f"Invalid steering file format: {path}")
    return raw


def save_steering(path: Path, steering: dict[str, Any]) -> None:
    write_json_atomic(path, steering)


def load_labels(paths: AppPaths) -> list[dict[str, Any]]:
    raw = read_json(paths.labels_path)
    if raw is None:
        return []
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    raise ValueError(f"Invalid labels format: {paths.labels_path}")


def save_labels(paths: AppPaths, labels: list[dict[str, Any]]) -> None:
    write_json_atomic(paths.labels_path, labels)


def load_assignments(paths: AppPaths) -> list[dict[str, Any]]:
    raw = read_json(paths.assignments_path)
    if raw is None:
        return []
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    raise ValueError(f"Invalid assignments format: {paths.assignments_path}")


def save_assignments(paths: AppPaths, assignments: list[dict[str, Any]]) -> None:
    write_json_atomic(paths.assignments_path, assignments)
