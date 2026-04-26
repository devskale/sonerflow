from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from .github_cli import GhError, ensure_gh_auth, list_starred_repos
from .store import (
    load_config,
    load_config_file,
    load_assignments,
    load_catalog,
    load_labels,
    normalize_repo,
    save_catalog,
    save_config,
    save_config_file,
)
from .util import app_paths, default_root_dir, ensure_dirs


def _project_config_path() -> Path:
    return Path.cwd() / ".ghsorter.json"

def _clean_url(s: str) -> str:
    return s.strip().strip("`").strip()


def _merge_config(store_cfg: dict, project_cfg: dict) -> dict:
    out = dict(store_cfg)
    store_llm = store_cfg.get("llm") if isinstance(store_cfg.get("llm"), dict) else {}
    project_llm = project_cfg.get("llm") if isinstance(project_cfg.get("llm"), dict) else {}
    if store_llm or project_llm:
        out["llm"] = dict(store_llm)
        out["llm"].update(dict(project_llm))
    for k, v in project_cfg.items():
        if k == "llm":
            continue
        out[k] = v
    return out


def _cmd_doctor(_: argparse.Namespace) -> int:
    try:
        ensure_gh_auth()
    except GhError as e:
        print(str(e), file=sys.stderr)
        return 2
    print("ok: gh auth")
    return 0


def _cmd_ingest(args: argparse.Namespace) -> int:
    paths = app_paths(args.store)
    ensure_dirs(paths)
    try:
        ensure_gh_auth()
        starred = list_starred_repos()
    except GhError as e:
        print(str(e), file=sys.stderr)
        return 2

    fetched_at = datetime.now(timezone.utc).isoformat()
    repos = []
    for repo in starred:
        repos.append(normalize_repo(repo, fetched_at=fetched_at))

    save_catalog(paths, repos, source="github:user/starred", fetched_at=fetched_at)
    print(f"catalog: {paths.catalog_path} ({len(repos)} repos)")
    return 0


def _cmd_config(args: argparse.Namespace) -> int:
    paths = app_paths(args.store)
    ensure_dirs(paths)
    cfg_path = None
    if getattr(args, "project", False):
        cfg_path = _project_config_path()
        cfg = load_config_file(cfg_path)
    else:
        cfg = load_config(paths)

    if args.action == "show":
        import json

        print(json.dumps(cfg, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if args.action == "set":
        llm = cfg.get("llm") if isinstance(cfg.get("llm"), dict) else {}
        llm = dict(llm)

        if args.model is not None:
            llm["model"] = args.model
        if getattr(args, "embed_model", None) is not None:
            llm["embed_model"] = args.embed_model
        if args.context_tokens is not None:
            llm["context_tokens"] = int(args.context_tokens)
        if args.base_url is not None:
            llm["base_url"] = _clean_url(args.base_url)
        if args.credgoo_key is not None:
            llm["credgoo_key"] = args.credgoo_key
        if args.model_fallback is not None:
            llm["model_fallbacks"] = list(args.model_fallback)

        cfg["llm"] = llm
        if cfg_path is not None:
            save_config_file(cfg_path, cfg)
            print(f"config: {cfg_path}")
        else:
            save_config(paths, cfg)
            print(f"config: {paths.config_path}")
        return 0

    print("invalid config action", file=sys.stderr)
    return 2


def _cmd_analyze(args: argparse.Namespace) -> int:
    from .analyze import analyze

    paths = app_paths(args.store)
    ensure_dirs(paths)
    store_cfg = load_config(paths)
    project_cfg = load_config_file(_project_config_path()) if _project_config_path().exists() else {}
    cfg = _merge_config(store_cfg, project_cfg)
    llm_cfg = cfg.get("llm") if isinstance(cfg.get("llm"), dict) else {}

    return analyze(
        paths,
        base_url=args.base_url or llm_cfg.get("base_url"),
        model=args.model or llm_cfg.get("model"),
        model_fallbacks=(args.model_fallback if args.model_fallback is not None else (llm_cfg.get("model_fallbacks") or [])),
        api_key=args.api_key,
        credgoo_service=args.credgoo_key or llm_cfg.get("credgoo_key"),
        context_tokens=args.context_tokens if args.context_tokens is not None else llm_cfg.get("context_tokens"),
        steer_path=args.steer,
        seed_areas_path=args.seed_areas,
        target_areas=args.target_areas,
        interactive_steering=args.interactive_steering,
        include_readme=args.readme,
        limit_repos=args.limit_repos,
        max_llm_calls=args.max_llm_calls,
        force=args.force,
        pause=args.pause,
        live=args.live,
    )


def _cmd_export(args: argparse.Namespace) -> int:
    from .export import export_outputs

    paths = app_paths(args.store)
    ensure_dirs(paths)
    catalog = load_catalog(paths)
    labels = load_labels(paths)
    assignments = load_assignments(paths)
    export_outputs(paths, catalog=catalog, labels=labels, assignments=assignments, formats=args.format, basename="export", title="ghsorter export")
    print(f"output: {paths.export_dir}")
    return 0


def _cmd_cluster(args: argparse.Namespace) -> int:
    from .cluster import run_cluster
    from .llm import OpenAICompatConfig, default_openai_compat_config

    paths = app_paths(args.store)
    ensure_dirs(paths)

    store_cfg = load_config(paths)
    project_cfg = load_config_file(_project_config_path()) if _project_config_path().exists() else {}
    cfg = _merge_config(store_cfg, project_cfg)
    llm_cfg = cfg.get("llm") if isinstance(cfg.get("llm"), dict) else {}

    try:
        llm: OpenAICompatConfig = default_openai_compat_config(
            base_url=llm_cfg.get("base_url"),
            model=llm_cfg.get("model"),
            model_fallbacks=(llm_cfg.get("model_fallbacks") or []),
            api_key=None,
            credgoo_service=llm_cfg.get("credgoo_key"),
            context_tokens=llm_cfg.get("context_tokens"),
        )
    except Exception as e:
        print(str(e), file=sys.stderr)
        return 2
    if not llm.api_key:
        print("Missing LLM API key. Set OPENAI_API_KEY, pass --api-key, or configure credgoo.", file=sys.stderr)
        return 2

    embed_model = args.embed_model or llm_cfg.get("embed_model") or "tu@qwen3-embedding-4b"
    cache_dir = (args.cache_dir or (Path.cwd() / ".ghsorter_cache")).expanduser()
    seed_file = args.seed_file.expanduser() if args.seed_file is not None else None

    return run_cluster(
        paths,
        cfg=llm,
        embed_model=embed_model,
        method=args.method,
        k=args.k,
        top_level=args.top_level,
        outlier_threshold=args.outlier_threshold,
        cache_dir=cache_dir,
        limit_repos=args.limit_repos,
        seed_file=seed_file,
    )


def _cmd_dosort(args: argparse.Namespace) -> int:
    from .analyze import analyze

    paths = app_paths(args.store)
    ensure_dirs(paths)

    catalog = load_catalog(paths)
    if not catalog.repos:
        rc = _cmd_ingest(args)
        if rc != 0:
            return rc

    store_cfg = load_config(paths)
    project_cfg = load_config_file(_project_config_path()) if _project_config_path().exists() else {}
    cfg = _merge_config(store_cfg, project_cfg)
    llm_cfg = cfg.get("llm") if isinstance(cfg.get("llm"), dict) else {}

    return analyze(
        paths,
        base_url=llm_cfg.get("base_url"),
        model=llm_cfg.get("model"),
        model_fallbacks=(llm_cfg.get("model_fallbacks") or []),
        api_key=None,
        credgoo_service=llm_cfg.get("credgoo_key"),
        context_tokens=llm_cfg.get("context_tokens"),
        steer_path=None,
        seed_areas_path=None,
        target_areas=None,
        interactive_steering=False,
        include_readme=False,
        limit_repos=None,
        max_llm_calls=None if args.full else 2,
        force=False,
        pause=args.pause,
        live=args.live,
    )


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ghsorter")
    p.add_argument(
        "--store",
        type=Path,
        default=default_root_dir(),
        help="Local store directory (default: ./.ghsorter_store if .ghsorter.json exists, else ~/.ghsorter)",
    )
    p.add_argument("--dosort", action="store_true", default=False, help="Shortcut: ingest (if needed) + analyze using config defaults")
    p.add_argument("--full", action="store_true", default=False, help="Used with --dosort: run full sorting (no LLM call cap)")
    p.add_argument("--pause", action="store_true", default=False, help="Pause after each LLM batch (resumable)")
    p.add_argument("--live", action="store_true", default=False, help="Write live outputs during sorting (output/live.md, output/live.json)")

    sub = p.add_subparsers(dest="cmd", required=False)

    d = sub.add_parser("doctor")
    d.add_argument("--store", type=Path, default=default_root_dir())
    d.set_defaults(func=_cmd_doctor)

    i = sub.add_parser("ingest")
    i.add_argument("--store", type=Path, default=default_root_dir())
    i.set_defaults(func=_cmd_ingest)

    c = sub.add_parser("config")
    csub = c.add_subparsers(dest="action", required=True)

    cshow = csub.add_parser("show")
    cshow.add_argument("--store", type=Path, default=default_root_dir())
    cshow.add_argument("--project", action="store_true", default=False)
    cshow.set_defaults(func=_cmd_config)

    cset = csub.add_parser("set")
    cset.add_argument("--store", type=Path, default=default_root_dir())
    cset.add_argument("--project", action="store_true", default=False)
    cset.add_argument("--model", default=None)
    cset.add_argument("--embed-model", dest="embed_model", default=None)
    cset.add_argument("--context-tokens", dest="context_tokens", type=int, default=None)
    cset.add_argument("--base-url", dest="base_url", default=None)
    cset.add_argument("--credgoo-key", dest="credgoo_key", default=None)
    cset.add_argument("--model-fallback", action="append", default=None)
    cset.set_defaults(func=_cmd_config)

    a = sub.add_parser("analyze")
    a.add_argument("--store", type=Path, default=default_root_dir())
    a.add_argument("--base-url", dest="base_url", default=None)
    a.add_argument("--model", default=None)
    a.add_argument("--model-fallback", action="append", default=None)
    a.add_argument("--api-key", dest="api_key", default=None)
    a.add_argument("--credgoo-key", dest="credgoo_key", default=None)
    a.add_argument("--context-tokens", dest="context_tokens", type=int, default=None)
    a.add_argument("--max-llm-calls", dest="max_llm_calls", type=int, default=None)
    a.add_argument("--force", action="store_true", default=False)
    a.add_argument("--readme", action="store_true", default=False)
    a.add_argument("--steer", type=Path, default=None)
    a.add_argument("--seed-areas", type=Path, default=None)
    a.add_argument("--target-areas", type=int, default=None)
    a.add_argument("--interactive-steering", action="store_true", default=False)
    a.add_argument("--limit-repos", dest="limit_repos", type=int, default=None)
    a.set_defaults(func=_cmd_analyze)

    e = sub.add_parser("export")
    e.add_argument("--store", type=Path, default=default_root_dir())
    e.add_argument("--format", choices=["json", "md", "both"], default="both")
    e.set_defaults(func=_cmd_export)

    cl = sub.add_parser("cluster")
    cl.add_argument("--store", type=Path, default=default_root_dir())
    cl.add_argument("--method", choices=["hdbscan", "kmeans"], default="hdbscan")
    cl.add_argument("--k", type=int, default=200)
    cl.add_argument("--top-level", dest="top_level", type=int, default=25)
    cl.add_argument("--outlier-threshold", dest="outlier_threshold", type=float, default=0.3)
    cl.add_argument("--embed-model", dest="embed_model", default=None)
    cl.add_argument("--cache-dir", dest="cache_dir", type=Path, default=None)
    cl.add_argument("--limit-repos", dest="limit_repos", type=int, default=None)
    cl.add_argument("--seed-file", dest="seed_file", type=Path, default=None)
    cl.set_defaults(func=_cmd_cluster)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "dosort", False) and getattr(args, "cmd", None) is None:
        return int(_cmd_dosort(args))
    if getattr(args, "cmd", None) is None:
        parser.print_help(sys.stderr)
        return 2
    return int(args.func(args))
