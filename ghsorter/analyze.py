from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import signal
import time
from typing import Any, Callable

from .export import export_outputs
from .llm import LlmError, OpenAICompatConfig, chat_completions_json, default_openai_compat_config
from .store import (
    Catalog,
    load_assignments,
    load_catalog,
    load_labels,
    load_steering,
    save_assignments,
    save_labels,
    save_steering,
)
from .util import AppPaths


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _repo_key(repo: dict[str, Any]) -> str | None:
    k = repo.get("full_name")
    return k if isinstance(k, str) and k else None


def _compact_repo(repo: dict[str, Any]) -> dict[str, Any]:
    stats = repo.get("stats") if isinstance(repo.get("stats"), dict) else {}
    stars = stats.get("stargazers_count") if isinstance(stats, dict) else None
    if stars is None:
        stars = repo.get("stargazers_count")
    stars = int(stars) if isinstance(stars, int) else None
    created_at = repo.get("created_at")
    pushed_at = repo.get("pushed_at") or repo.get("updated_at")

    return {
        "full_name": repo.get("full_name"),
        "description": repo.get("description"),
        "topics": repo.get("topics") or [],
        "language": repo.get("language"),
        "archived": bool(repo.get("archived") or False),
        "fork": bool(repo.get("fork") or False),
        "stars": stars,
        "created_at": created_at,
        "pushed_at": pushed_at,
        "popularity": _popularity_score(stars=stars, created_at=created_at, pushed_at=pushed_at),
    }


def _parse_dt(s: Any) -> datetime | None:
    if not isinstance(s, str) or not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _popularity_score(*, stars: int | None, created_at: Any, pushed_at: Any) -> dict[str, Any] | None:
    if stars is None:
        return None
    created = _parse_dt(created_at)
    pushed = _parse_dt(pushed_at)
    now = datetime.now(timezone.utc)
    age_days = (now - created).days if created else None
    recency_days = (now - pushed).days if pushed else None
    stars_per_year = None
    if age_days is not None and age_days > 0:
        stars_per_year = stars / (age_days / 365.0)
    return {"stars": stars, "stars_per_year": stars_per_year, "recency_days": recency_days}


def _chunk(xs: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [xs[i : i + size] for i in range(0, len(xs), size)]


def _load_seed_areas(path: Path) -> list[dict[str, Any]]:
    import json

    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and isinstance(raw.get("seed_areas"), list):
        raw = raw["seed_areas"]
    if not isinstance(raw, list):
        raise ValueError("seed areas file must be a JSON list or {seed_areas:[...]}")
    return [x for x in raw if isinstance(x, dict) and isinstance(x.get("name"), str)]


def _default_steering(paths: AppPaths) -> dict[str, Any]:
    return {
        "version": "1",
        "intent": "",
        "constraints": {
            "target_area_count": 20,
            "allow_multi_label": True,
            "max_areas_per_repo": 3,
            "naming_style": "concise",
            "overlap_policy": "minimize",
        },
        "seed_areas": [],
        "examples": {"together": [], "separate": []},
        "notes": "",
        "meta": {"created_at": _now_iso(), "updated_at": _now_iso()},
    }


def _interactive_steering(existing: dict[str, Any]) -> dict[str, Any]:
    intent = input("Intent (one sentence, optional): ").strip()
    target = input("Target number of areas (default 20): ").strip()
    seed = input("Seed areas (comma-separated names, optional): ").strip()

    if intent:
        existing["intent"] = intent
    if target:
        try:
            existing.setdefault("constraints", {})["target_area_count"] = int(target)
        except ValueError:
            pass
    if seed:
        areas = []
        for name in [x.strip() for x in seed.split(",") if x.strip()]:
            areas.append({"name": name})
        existing["seed_areas"] = areas

    existing.setdefault("meta", {})["updated_at"] = _now_iso()
    return existing


def _proposal_prompt(steering: dict[str, Any], sample_repos: list[dict[str, Any]]) -> tuple[str, str, str]:
    system = "You are a careful information architect. You group GitHub repositories into interest areas."
    schema = '{ "areas": [ { "id": "string", "name": "string", "description": "string" } ] }'
    target = None
    try:
        target_val = (steering.get("constraints") or {}).get("target_area_count")
        target = int(target_val) if target_val is not None else None
    except Exception:
        target = None
    parts = [
        "Create interest areas for a user's starred GitHub repositories.",
        "Requirements:",
        "- Return ONLY valid JSON.",
        f"- JSON schema: {schema}",
        "- ids must be stable, lowercase, and url-safe (slug-like)",
        "- Prefer practical, tool-oriented categories; avoid overly broad buckets.",
    ]
    if target and target > 0:
        parts.append(f"- Create AT MOST {target} areas.")
    steering_s = json.dumps(steering, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    sample_s = json.dumps(sample_repos, ensure_ascii=False, separators=(",", ":"))
    parts.extend(
        [
            "- Incorporate steering intent, constraints, and seed areas.",
            "",
            f"STEERING:\n{steering_s}",
            "",
            f"REPO SAMPLE (compact metadata):\n{sample_s}",
        ]
    )
    user = "\n".join(parts) + "\n"
    return system, user, schema


def _assign_prompt(steering: dict[str, Any], areas: list[dict[str, Any]], repos: list[dict[str, Any]]) -> tuple[str, str, str]:
    system = "You are a precise classifier. Assign each repository to 0..N interest areas."
    schema = '{ "assignments": [ { "full_name": "owner/repo", "area_ids": ["string"], "confidence": 0.0, "rationale": "string" } ] }'
    steering_s = json.dumps(steering, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    areas_s = json.dumps(areas, ensure_ascii=False, separators=(",", ":"))
    repos_s = json.dumps(repos, ensure_ascii=False, separators=(",", ":"))
    user = (
        "Assign repositories to interest areas.\n"
        "Requirements:\n"
        "- Return ONLY valid JSON.\n"
        f"- JSON schema: {schema}\n"
        "- Only use area_ids that exist in the provided areas list.\n"
        "- If unsure, return an empty list for area_ids.\n"
        "- Keep rationales short.\n"
        "- Respect steering constraints (multi-label, max areas per repo).\n\n"
        f"STEERING:\n{steering_s}\n\n"
        f"AREAS:\n{areas_s}\n\n"
        f"REPOS:\n{repos_s}\n"
    )
    return system, user, schema


def _approx_tokens(s: str) -> int:
    return max(1, len(s) // 2)


def _prompt_tokens(system: str, user: str) -> int:
    return _approx_tokens(system) + _approx_tokens(user) + 64


def _max_output_tokens(*, context_tokens: int | None, system: str, user: str, desired_max: int) -> int | None:
    if context_tokens is None or context_tokens <= 0:
        return None
    available = context_tokens - _prompt_tokens(system, user)
    if available <= 64:
        return 64
    return max(64, min(int(desired_max), int(available - 16)))


def _fit_repos_to_context(
    *,
    context_tokens: int | None,
    output_reserve_tokens: int,
    make_prompt: Callable[[list[dict[str, Any]]], tuple[str, str, str]],
    repos: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if context_tokens is None or context_tokens <= 0:
        return repos
    keep = repos
    while True:
        system, user, _ = make_prompt(keep)
        if _prompt_tokens(system, user) <= max(1, context_tokens - output_reserve_tokens):
            return keep
        if len(keep) <= 1:
            return keep
        keep = keep[: max(1, len(keep) // 2)]


def analyze(
    paths: AppPaths,
    *,
    base_url: str | None,
    model: str | None,
    model_fallbacks: list[str],
    api_key: str | None,
    credgoo_service: str | None,
    context_tokens: int | None,
    steer_path: Path | None,
    seed_areas_path: Path | None,
    target_areas: int | None,
    interactive_steering: bool,
    include_readme: bool,
    limit_repos: int | None,
    max_llm_calls: int | None,
    force: bool,
    pause: bool,
    live: bool,
) -> int:
    if include_readme:
        print("--readme is not implemented yet", file=sys.stderr)
        return 2

    catalog = load_catalog(paths)
    if not catalog.repos:
        print("catalog is empty; run: ghsorter ingest", file=sys.stderr)
        return 2
    if limit_repos is not None and limit_repos >= 0:
        catalog = Catalog(repos=catalog.repos[:limit_repos], meta=catalog.meta)

    steering_path = (steer_path or paths.steering_path).expanduser()
    steering = load_steering(steering_path) or _default_steering(paths)
    if target_areas is not None:
        steering.setdefault("constraints", {})["target_area_count"] = target_areas
    if seed_areas_path:
        steering["seed_areas"] = _load_seed_areas(seed_areas_path.expanduser())
    if interactive_steering:
        steering = _interactive_steering(steering)
    steering.setdefault("meta", {})["updated_at"] = _now_iso()
    save_steering(steering_path, steering)

    try:
        cfg: OpenAICompatConfig = default_openai_compat_config(
            base_url=base_url,
            model=model,
            model_fallbacks=model_fallbacks,
            api_key=api_key,
            credgoo_service=credgoo_service,
            context_tokens=context_tokens,
        )
    except Exception as e:
        print(str(e), file=sys.stderr)
        return 2
    if not cfg.api_key:
        print("Missing LLM API key. Set OPENAI_API_KEY, pass --api-key, or configure credgoo (--credgoo-key amd1).", file=sys.stderr)
        return 2
    print(f"llm: base_url={cfg.base_url} model={cfg.model}", file=sys.stderr)

    llm_calls = 0
    started_at = time.monotonic()
    stop_requested = False

    def _on_sigint(_signum: int, _frame: object) -> None:
        nonlocal stop_requested
        stop_requested = True

    try:
        signal.signal(signal.SIGINT, _on_sigint)
    except Exception:
        pass
    if max_llm_calls is not None and max_llm_calls <= 0:
        print("max_llm_calls is 0; skipping LLM calls")
        return 0


    labels = load_labels(paths)
    if not labels:
        if max_llm_calls is not None and llm_calls >= max_llm_calls:
            print("Reached max_llm_calls before area proposal; stopping")
            return 0
        output_reserve = max(512, int((cfg.context_tokens or 0) * 0.25)) if cfg.context_tokens else 2048
        print("step: propose areas (smoke test)", file=sys.stderr)
        raw_sample = [_compact_repo(r) for r in catalog.repos[:200]]
        sample = _fit_repos_to_context(
            context_tokens=cfg.context_tokens,
            output_reserve_tokens=output_reserve,
            make_prompt=lambda keep: _proposal_prompt(steering, keep),
            repos=raw_sample,
        )
        system, user, schema = _proposal_prompt(steering, sample)
        try:
            res = chat_completions_json(
                cfg,
                system=system,
                user=user,
                response_schema_hint=schema,
                temperature=0.2,
                max_tokens=_max_output_tokens(context_tokens=cfg.context_tokens, system=system, user=user, desired_max=output_reserve),
            )
            llm_calls += 1
        except LlmError as e:
            print(str(e), file=sys.stderr)
            return 2
        areas = res.get("areas") if isinstance(res, dict) else None
        if not isinstance(areas, list):
            print("LLM returned invalid areas payload", file=sys.stderr)
            return 2
        labels = []
        for a in areas:
            if not isinstance(a, dict):
                continue
            area_id = a.get("id")
            name = a.get("name")
            if not isinstance(area_id, str) or not isinstance(name, str):
                continue
            labels.append(
                {
                    "id": area_id,
                    "name": name,
                    "description": a.get("description") if isinstance(a.get("description"), str) else "",
                    "created_at": _now_iso(),
                    "updated_at": _now_iso(),
                }
            )
        target = (steering.get("constraints") or {}).get("target_area_count")
        try:
            target = int(target) if target is not None else None
        except Exception:
            target = None
        if target and target > 0 and len(labels) > target:
            print(f"warning: model returned {len(labels)} areas; truncating to {target}", file=sys.stderr)
            labels = labels[:target]
        if not labels:
            print("No valid areas produced by LLM", file=sys.stderr)
            return 2
        save_labels(paths, labels)
        if live:
            export_outputs(paths, catalog=catalog, labels=labels, assignments=[], formats="both", basename="live", title="ghsorter live")

    locked_map: dict[str, dict[str, Any]] = {}
    existing_map: dict[str, dict[str, Any]] = {}
    existing_assignments = load_assignments(paths)
    for a in existing_assignments:
        if not isinstance(a, dict):
            continue
        repo_id = a.get("repo_id")
        if isinstance(repo_id, str) and repo_id:
            existing_map[repo_id] = a
            if a.get("locked") is True:
                locked_map[repo_id] = a

    catalog_ids = [k for k in (_repo_key(r) for r in catalog.repos) if k]
    total_target = len(catalog_ids)

    to_assign = []
    for repo in catalog.repos:
        k = _repo_key(repo)
        if not k or k in locked_map:
            continue
        if not force and k in existing_map:
            continue
        to_assign.append(_compact_repo(repo))

    assignments_out: list[dict[str, Any]] = list(locked_map.values())
    if not force:
        for rid, a in existing_map.items():
            if rid in locked_map:
                continue
            assignments_out.append(a)

    max_per_repo = steering.get("constraints", {}).get("max_areas_per_repo")
    max_per_repo = int(max_per_repo) if isinstance(max_per_repo, int) or (isinstance(max_per_repo, float) and max_per_repo.is_integer()) else None

    def _fmt_eta(seconds: float) -> str:
        if seconds < 0:
            return "?"
        m, s = divmod(int(seconds), 60)
        h, m = divmod(m, 60)
        if h:
            return f"{h}h{m:02d}m"
        if m:
            return f"{m}m{s:02d}s"
        return f"{s}s"

    def _progress(done: int, total: int, *, processed: int, llm_calls_now: int, remaining_items: int) -> str:
        pct = 0.0 if total <= 0 else (done / total) * 100.0
        elapsed = max(0.001, time.monotonic() - started_at)
        rate = processed / elapsed if processed > 0 else 0.0
        eta = (remaining_items / rate) if rate > 0 else -1.0
        return f"{done}/{total} ({pct:.1f}%) | llm_calls={llm_calls_now} | {rate:.1f} repos/s | eta={_fmt_eta(eta)}"

    def _area_counts() -> list[tuple[str, int]]:
        counts: dict[str, int] = {}
        for a in assignments_out:
            cids = a.get("category_ids")
            if not isinstance(cids, list):
                continue
            for cid in cids:
                if isinstance(cid, str) and cid:
                    counts[cid] = counts.get(cid, 0) + 1
        return sorted(counts.items(), key=lambda x: (-x[1], x[0]))

    def _print_area_summary(top: int = 10) -> None:
        label_name = {l.get("id"): l.get("name") for l in labels if isinstance(l, dict)}
        items = _area_counts()[:top]
        if not items:
            print("areas: (no assignments yet)", file=sys.stderr)
            return
        summary = ", ".join([f"{label_name.get(cid) or cid}:{n}" for cid, n in items])
        print(f"areas(top{top}): {summary}", file=sys.stderr)

    output_reserve = max(512, int((cfg.context_tokens or 0) * 0.35)) if cfg.context_tokens else 2048
    remaining = list(to_assign)
    done_initial = total_target - len(remaining)
    done_so_far = done_initial
    print(
        f"progress: {_progress(done_so_far, total_target, processed=0, llm_calls_now=llm_calls, remaining_items=len(remaining))}",
        file=sys.stderr,
    )
    while remaining:
        if stop_requested:
            save_assignments(paths, assignments_out)
            export_outputs(paths, catalog=catalog, labels=labels, assignments=assignments_out, formats="both", basename="export", title="ghsorter export")
            processed = max(0, done_so_far - done_initial)
            print(
                f"stopped: interrupted; rerun to continue (progress: {_progress(done_so_far, total_target, processed=processed, llm_calls_now=llm_calls, remaining_items=len(remaining))})",
                file=sys.stderr,
            )
            return 130

        if max_llm_calls is not None and llm_calls >= max_llm_calls:
            save_assignments(paths, assignments_out)
            export_outputs(paths, catalog=catalog, labels=labels, assignments=assignments_out, formats="both", basename="export", title="ghsorter export")
            processed = max(0, done_so_far - done_initial)
            print(
                f"stopped: max_llm_calls reached ({llm_calls}); rerun to continue (progress: {_progress(done_so_far, total_target, processed=processed, llm_calls_now=llm_calls, remaining_items=len(remaining))})",
                file=sys.stderr,
            )
            return 0

        batch = remaining[:50]
        batch = _fit_repos_to_context(
            context_tokens=cfg.context_tokens,
            output_reserve_tokens=output_reserve,
            make_prompt=lambda keep: _assign_prompt(steering, labels, keep),
            repos=batch,
        )
        system, user, schema = _assign_prompt(steering, labels, batch)
        try:
            res = chat_completions_json(
                cfg,
                system=system,
                user=user,
                response_schema_hint=schema,
                temperature=0.1,
                max_tokens=_max_output_tokens(context_tokens=cfg.context_tokens, system=system, user=user, desired_max=output_reserve),
            )
            llm_calls += 1
        except LlmError as e:
            print(str(e), file=sys.stderr)
            return 2
        items = res.get("assignments") if isinstance(res, dict) else None
        if not isinstance(items, list):
            print("LLM returned invalid assignments payload", file=sys.stderr)
            return 2
        for item in items:
            if not isinstance(item, dict):
                continue
            full_name = item.get("full_name")
            if not isinstance(full_name, str) or not full_name:
                continue
            area_ids = item.get("area_ids")
            if not isinstance(area_ids, list):
                area_ids = []
            area_ids = [x for x in area_ids if isinstance(x, str)]
            if max_per_repo is not None:
                area_ids = area_ids[: max(0, max_per_repo)]
            confidence = item.get("confidence")
            if not isinstance(confidence, (int, float)):
                confidence = None
            rationale = item.get("rationale")
            if not isinstance(rationale, str):
                rationale = ""
            assignments_out.append(
                {
                    "repo_id": full_name,
                    "category_ids": area_ids,
                    "locked": False,
                    "confidence": float(confidence) if confidence is not None else None,
                    "rationale": rationale,
                }
            )
        if live:
            export_outputs(paths, catalog=catalog, labels=labels, assignments=assignments_out, formats="both", basename="live", title="ghsorter live")
            _print_area_summary()
        save_assignments(paths, assignments_out)
        remaining = remaining[len(batch) :]
        done_so_far = total_target - len(remaining)
        processed = max(0, done_so_far - done_initial)
        print(
            f"progress: {_progress(done_so_far, total_target, processed=processed, llm_calls_now=llm_calls, remaining_items=len(remaining))}",
            file=sys.stderr,
        )
        if pause:
            ans = input("pause: press Enter to continue, or type 'q' to stop > ").strip().lower()
            if ans in ("q", "quit", "exit", "n", "no"):
                export_outputs(paths, catalog=catalog, labels=labels, assignments=assignments_out, formats="both", basename="export", title="ghsorter export")
                print("stopped: paused; rerun to continue", file=sys.stderr)
                return 0

    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for a in assignments_out:
        repo_id = a.get("repo_id")
        if not isinstance(repo_id, str) or not repo_id or repo_id in seen:
            continue
        seen.add(repo_id)
        deduped.append(a)

    save_assignments(paths, deduped)
    export_outputs(paths, catalog=catalog, labels=labels, assignments=deduped, formats="both", basename="export", title="ghsorter export")
    print(f"labels: {paths.labels_path} ({len(labels)} areas)")
    print(f"assignments: {paths.assignments_path} ({len(deduped)} repos)")
    print(f"output: {paths.export_dir}")
    return 0
