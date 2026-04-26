from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import numpy as np
from sklearn.cluster import AgglomerativeClustering, KMeans

from .export import export_outputs
from .llm import OpenAICompatConfig, embeddings_vectors
from .store import Catalog, load_catalog, save_assignments, save_labels
from .util import AppPaths, write_json_atomic


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(s: str) -> str:
    out = []
    for ch in s.lower():
        if ch.isalnum():
            out.append(ch)
        elif ch in (" ", "-", "_", "/", "."):
            out.append("-")
    v = "".join(out)
    while "--" in v:
        v = v.replace("--", "-")
    return v.strip("-") or "misc"


def _repo_id(repo: dict[str, Any]) -> str | None:
    rid = repo.get("full_name")
    return rid if isinstance(rid, str) and rid else None


def _repo_profile(repo: dict[str, Any]) -> str:
    topics = repo.get("topics")
    if not isinstance(topics, list):
        topics = []
    topics = [t for t in topics if isinstance(t, str) and t]
    language = repo.get("language") if isinstance(repo.get("language"), str) else ""
    desc = repo.get("description") if isinstance(repo.get("description"), str) else ""
    signals = repo.get("signals") if isinstance(repo.get("signals"), dict) else {}
    stars = signals.get("stars")
    recency_days = signals.get("recency_days")
    stars_per_month = signals.get("stars_per_month")
    parts = []
    if topics:
        parts.append("topics: " + ", ".join(topics[:20]))
    if language:
        parts.append("language: " + language)
    if desc:
        parts.append("description: " + desc.strip())
    parts.append(f"stars: {stars}" if isinstance(stars, int) else "stars: ?")
    parts.append(f"recency_days: {recency_days}" if isinstance(recency_days, int) else "recency_days: ?")
    if isinstance(stars_per_month, (int, float)):
        parts.append(f"stars_per_month: {stars_per_month:.3f}")
    return "\n".join(parts).strip()


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na <= 0 or nb <= 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def _cache_paths(cache_dir: Path) -> tuple[Path, Path]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / "embeddings.jsonl", cache_dir / "meta.json"


def _repo_signature(repo: dict[str, Any]) -> str:
    rid = _repo_id(repo) or ""
    updated_at = repo.get("updated_at") if isinstance(repo.get("updated_at"), str) else ""
    pushed_at = repo.get("pushed_at") if isinstance(repo.get("pushed_at"), str) else ""
    stars = None
    stats = repo.get("stats") if isinstance(repo.get("stats"), dict) else {}
    if isinstance(stats, dict) and isinstance(stats.get("stargazers_count"), int):
        stars = stats.get("stargazers_count")
    desc = repo.get("description") if isinstance(repo.get("description"), str) else ""
    topics = repo.get("topics")
    if not isinstance(topics, list):
        topics = []
    topics_s = ",".join(sorted([t for t in topics if isinstance(t, str)]))
    raw = f"{rid}|{updated_at}|{pushed_at}|{stars}|{topics_s}|{desc}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _load_embedding_cache(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    out: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        rid = obj.get("repo_id")
        if not isinstance(rid, str) or not rid:
            continue
        out[rid] = obj
    return out


def _save_embedding_cache(path: Path, rows: list[dict[str, Any]]) -> None:
    payload = "\n".join([json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in rows]) + "\n"
    path.write_text(payload, encoding="utf-8")


def _load_seed_lists(path: Path) -> dict[str, set[str]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, set[str]] = {}
    if isinstance(raw, dict) and isinstance(raw.get("lists"), dict):
        raw = raw["lists"]
    if isinstance(raw, dict):
        for k, v in raw.items():
            if not isinstance(k, str) or not k:
                continue
            if isinstance(v, list):
                out[k] = {x for x in v if isinstance(x, str) and x}
        return out
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            repos = item.get("repos")
            if not isinstance(name, str) or not isinstance(repos, list):
                continue
            out[name] = {x for x in repos if isinstance(x, str) and x}
        return out
    raise ValueError("seed file must be {lists:{name:[repo,...]}} or [{name,repos:[...]}]")


def _top_terms(repos: list[dict[str, Any]], *, max_terms: int = 3) -> list[str]:
    counts: dict[str, float] = {}
    rename = {"claude-code": "coding-agent"}
    for r in repos:
        topics = r.get("topics")
        if isinstance(topics, list):
            for t in topics:
                if isinstance(t, str) and t:
                    t = rename.get(t, t)
                    counts[t] = counts.get(t, 0.0) + 1.0
        lang = r.get("language")
        if isinstance(lang, str) and lang:
            lang = rename.get(lang, lang)
            counts[lang] = counts.get(lang, 0.0) + 0.25
    items = sorted(counts.items(), key=lambda x: (-x[1], x[0].lower()))
    return [k for k, _ in items[:max_terms]]


@dataclass(frozen=True)
class ClusterOutputs:
    labels: list[dict[str, Any]]
    assignments: list[dict[str, Any]]


def cluster_repos(
    paths: AppPaths,
    *,
    cfg: OpenAICompatConfig,
    embed_model: str,
    method: Literal["hdbscan", "kmeans"],
    k: int,
    top_level: int,
    outlier_threshold: float,
    cache_dir: Path,
    limit_repos: int | None,
    seed_file: Path | None,
) -> ClusterOutputs:
    catalog = load_catalog(paths)
    repos = catalog.repos
    if limit_repos is not None and limit_repos >= 0:
        repos = repos[:limit_repos]
        catalog = Catalog(repos=repos, meta=catalog.meta)

    id_to_repo: dict[str, dict[str, Any]] = {}
    repo_ids: list[str] = []
    profiles: list[str] = []
    for r in repos:
        if not isinstance(r, dict):
            continue
        rid = _repo_id(r)
        if not rid:
            continue
        repo_ids.append(rid)
        id_to_repo[rid] = r
        profiles.append(_repo_profile(r))

    emb_path, meta_path = _cache_paths(cache_dir)
    cache = _load_embedding_cache(emb_path)

    sigs = {rid: _repo_signature(id_to_repo[rid]) for rid in repo_ids}
    missing: list[str] = []
    for rid in repo_ids:
        row = cache.get(rid)
        if not isinstance(row, dict) or row.get("sig") != sigs[rid] or not isinstance(row.get("embedding"), list):
            missing.append(rid)

    if missing:
        batch_inputs: list[str] = []
        batch_ids: list[str] = []
        profile_by_id = {repo_ids[i]: profiles[i] for i in range(len(repo_ids))}
        for rid in missing:
            batch_ids.append(rid)
            batch_inputs.append(profile_by_id[rid])
            if len(batch_inputs) >= 64:
                vecs = embeddings_vectors(cfg, model=embed_model, inputs=batch_inputs)
                for i, v in enumerate(vecs):
                    cache[batch_ids[i]] = {"repo_id": batch_ids[i], "sig": sigs[batch_ids[i]], "embedding": v}
                batch_inputs = []
                batch_ids = []
        if batch_inputs:
            vecs = embeddings_vectors(cfg, model=embed_model, inputs=batch_inputs)
            for i, v in enumerate(vecs):
                cache[batch_ids[i]] = {"repo_id": batch_ids[i], "sig": sigs[batch_ids[i]], "embedding": v}

        _save_embedding_cache(emb_path, list(cache.values()))
        write_json_atomic(meta_path, {"updated_at": _now_iso(), "embed_model": embed_model, "count": len(cache)})

    vectors: list[list[float]] = []
    kept_ids: list[str] = []
    for rid in repo_ids:
        row = cache.get(rid) or {}
        emb = row.get("embedding")
        if isinstance(emb, list) and emb and all(isinstance(x, (int, float)) for x in emb):
            vectors.append([float(x) for x in emb])
            kept_ids.append(rid)

    X = np.array(vectors, dtype=np.float32)
    if seed_file is not None and seed_file.exists():
        seed_lists = _load_seed_lists(seed_file)
        repo_to_lists: dict[str, list[str]] = {}
        for name, reposet in seed_lists.items():
            for rid in reposet:
                if rid in id_to_repo:
                    repo_to_lists.setdefault(rid, []).append(name)

        list_names = [n for n in seed_lists.keys() if any(r in id_to_repo for r in seed_lists[n])]
        list_names.sort(key=lambda s: s.lower())

        list_centroids: list[np.ndarray] = []
        kept_list_names: list[str] = []
        id_index = {kept_ids[i]: i for i in range(len(kept_ids))}
        for name in list_names:
            idx = [id_index[r] for r in seed_lists[name] if r in id_index]
            if not idx:
                continue
            kept_list_names.append(name)
            list_centroids.append(np.mean(X[idx, :], axis=0))

        if not kept_list_names:
            labels_out = [{"id": "misc", "name": "Misc", "description": "Uncategorized.", "created_at": _now_iso(), "updated_at": _now_iso()}]
            assignments_out = [{"repo_id": rid, "category_ids": ["misc"], "locked": False} for rid in kept_ids]
            return ClusterOutputs(labels=labels_out, assignments=assignments_out)

        max_top = max(1, int(top_level))
        centroids = np.stack(list_centroids, axis=0)
        if len(kept_list_names) > max_top:
            agg = AgglomerativeClustering(n_clusters=max_top)
            groups = agg.fit_predict(centroids)
        else:
            groups = np.arange(len(kept_list_names), dtype=int)

        group_to_names: dict[int, list[str]] = {}
        for i, g in enumerate(groups):
            group_to_names.setdefault(int(g), []).append(kept_list_names[i])

        group_ids = sorted(group_to_names.keys())
        meta_id_by_group: dict[int, str] = {g: f"meta-{i}" for i, g in enumerate(group_ids)}
        meta_centroid_by_group: dict[int, np.ndarray] = {}
        for g in group_ids:
            idx = [i for i, gg in enumerate(groups) if int(gg) == int(g)]
            meta_centroid_by_group[g] = np.mean(centroids[idx, :], axis=0)

        labels_out: list[dict[str, Any]] = []
        for g in group_ids:
            names = group_to_names[g]
            display = names[0] if names else f"Meta {g}"
            labels_out.append(
                {
                    "id": meta_id_by_group[g],
                    "name": display,
                    "description": "merged: " + ", ".join(names),
                    "created_at": _now_iso(),
                    "updated_at": _now_iso(),
                }
            )
        labels_out.append({"id": "misc", "name": "Misc", "description": "Uncategorized.", "created_at": _now_iso(), "updated_at": _now_iso()})

        name_to_group: dict[str, int] = {}
        for i, n in enumerate(kept_list_names):
            name_to_group[n] = int(groups[i])

        assignments_out: list[dict[str, Any]] = []
        for rid, vec in zip(kept_ids, X):
            lsts = repo_to_lists.get(rid) or []
            if lsts:
                metas = sorted({meta_id_by_group[name_to_group[n]] for n in lsts if n in name_to_group})
                if not metas:
                    metas = ["misc"]
                assignments_out.append({"repo_id": rid, "category_ids": metas, "locked": True})
                continue

            best_g = None
            best_score = 0.0
            for g in group_ids:
                s = _cosine(vec, meta_centroid_by_group[g])
                if s > best_score:
                    best_score = s
                    best_g = g
            suggested_meta = meta_id_by_group.get(best_g) if best_g is not None else None
            category_ids = [suggested_meta] if suggested_meta and float(best_score) >= float(outlier_threshold) else ["misc"]
            assignments_out.append(
                {
                    "repo_id": rid,
                    "category_ids": category_ids,
                    "locked": False,
                    "suggested_meta_id": suggested_meta,
                    "suggested_score": float(best_score),
                }
            )

        return ClusterOutputs(labels=labels_out, assignments=assignments_out)

    if method == "hdbscan":
        import hdbscan  # type: ignore

        norms = np.linalg.norm(X, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        Xn = X / norms
        clusterer = hdbscan.HDBSCAN(min_cluster_size=5, min_samples=1, metric="euclidean")
        leaf_labels = clusterer.fit_predict(Xn)
    else:
        km = KMeans(n_clusters=int(k), random_state=0, n_init="auto")
        leaf_labels = km.fit_predict(X)

    leaf_to_ids: dict[int, list[str]] = {}
    for rid, lbl in zip(kept_ids, leaf_labels):
        leaf_to_ids.setdefault(int(lbl), []).append(rid)

    leaf_ids = [lbl for lbl in leaf_to_ids.keys() if lbl != -1]
    leaf_centroids: dict[int, np.ndarray] = {}
    for lbl in leaf_ids:
        idx = [i for i, l in enumerate(leaf_labels) if int(l) == int(lbl)]
        leaf_centroids[lbl] = np.mean(X[idx, :], axis=0)

    if not leaf_ids:
        labels_out = [{"id": "misc", "name": "Misc", "description": "Uncategorized.", "created_at": _now_iso(), "updated_at": _now_iso()}]
        assignments_out = [{"repo_id": rid, "category_ids": ["misc"], "locked": False} for rid in kept_ids]
        return ClusterOutputs(labels=labels_out, assignments=assignments_out)

    centroid_matrix = np.stack([leaf_centroids[l] for l in leaf_ids], axis=0)
    top_k = min(int(top_level), len(leaf_ids))
    agg = AgglomerativeClustering(n_clusters=top_k)
    top_labels = agg.fit_predict(centroid_matrix)
    leaf_to_top: dict[int, int] = {leaf_ids[i]: int(top_labels[i]) for i in range(len(leaf_ids))}

    top_to_leaf: dict[int, list[int]] = {}
    for leaf, top in leaf_to_top.items():
        top_to_leaf.setdefault(top, []).append(leaf)

    labels_out: list[dict[str, Any]] = []
    top_id_map: dict[int, str] = {}
    leaf_id_map: dict[int, str] = {}

    used_meta_names: set[str] = set()
    for top in sorted(top_to_leaf.keys()):
        leafs = top_to_leaf[top]
        repos_in_top = [id_to_repo[rid] for leaf in leafs for rid in leaf_to_ids.get(leaf, []) if rid in id_to_repo]
        candidates = _top_terms(repos_in_top, max_terms=8)
        picked = None
        for c in candidates:
            if c not in used_meta_names:
                picked = c
                break
        if picked is None:
            picked = candidates[0] if candidates else f"Meta {top + 1}"
        if picked in used_meta_names:
            i = 2
            while f"{picked}-{i}" in used_meta_names:
                i += 1
            picked = f"{picked}-{i}"
        used_meta_names.add(picked)
        name = picked
        cid = f"meta-{top}"
        top_id_map[top] = cid
        labels_out.append({"id": cid, "name": name, "description": "", "created_at": _now_iso(), "updated_at": _now_iso()})

    for leaf in sorted(leaf_ids):
        repo_objs = [id_to_repo[rid] for rid in leaf_to_ids.get(leaf, []) if rid in id_to_repo]
        terms = _top_terms(repo_objs, max_terms=3)
        name = " / ".join(terms) if terms else f"Cluster {leaf}"
        cid = f"leaf-{leaf}"
        leaf_id_map[leaf] = cid
        labels_out.append(
            {
                "id": cid,
                "name": name,
                "description": "",
                "parent_id": top_id_map.get(leaf_to_top.get(leaf, 0)),
                "created_at": _now_iso(),
                "updated_at": _now_iso(),
            }
        )

    labels_out.append({"id": "misc", "name": "Misc", "description": "Uncategorized / outliers.", "created_at": _now_iso(), "updated_at": _now_iso()})

    assignments_out: list[dict[str, Any]] = []
    for rid, vec, lbl in zip(kept_ids, X, leaf_labels):
        lbl = int(lbl)
        if lbl == -1:
            best_leaf = None
            best_score = 0.0
            for leaf in leaf_ids:
                s = _cosine(vec, leaf_centroids[leaf])
                if s > best_score:
                    best_score = s
                    best_leaf = leaf
            suggested_leaf_id = leaf_id_map.get(best_leaf) if best_leaf is not None else None
            suggested_meta_id = top_id_map.get(leaf_to_top.get(best_leaf)) if best_leaf is not None else None
            category_ids: list[str] = ["misc"]
            if suggested_meta_id and float(best_score) >= float(outlier_threshold):
                category_ids = [suggested_meta_id]
            assignments_out.append(
                {
                    "repo_id": rid,
                    "category_ids": category_ids,
                    "locked": False,
                    "suggested_category_id": suggested_leaf_id,
                    "suggested_meta_id": suggested_meta_id,
                    "suggested_score": float(best_score),
                }
            )
            continue

        meta_id = top_id_map.get(leaf_to_top.get(lbl, 0))
        leaf_id = leaf_id_map.get(lbl)
        centroid = leaf_centroids.get(lbl)
        score = _cosine(vec, centroid) if centroid is not None else 0.0
        if method == "kmeans" and score < float(outlier_threshold):
            suggested_leaf = leaf_id
            suggested_meta = meta_id
            assignments_out.append(
                {
                    "repo_id": rid,
                    "category_ids": ["misc"],
                    "locked": False,
                    "suggested_category_id": suggested_leaf,
                    "suggested_meta_id": suggested_meta,
                    "suggested_score": float(score),
                }
            )
        else:
            category_ids = [x for x in [meta_id, leaf_id] if isinstance(x, str) and x]
            assignments_out.append({"repo_id": rid, "category_ids": category_ids, "locked": False, "confidence": float(score)})

    return ClusterOutputs(labels=labels_out, assignments=assignments_out)


def run_cluster(
    paths: AppPaths,
    *,
    cfg: OpenAICompatConfig,
    embed_model: str,
    method: Literal["hdbscan", "kmeans"],
    k: int,
    top_level: int,
    outlier_threshold: float,
    cache_dir: Path,
    limit_repos: int | None,
    seed_file: Path | None,
) -> int:
    out = cluster_repos(
        paths,
        cfg=cfg,
        embed_model=embed_model,
        method=method,
        k=k,
        top_level=top_level,
        outlier_threshold=outlier_threshold,
        cache_dir=cache_dir,
        limit_repos=limit_repos,
        seed_file=seed_file,
    )
    save_labels(paths, out.labels)
    save_assignments(paths, out.assignments)
    catalog = load_catalog(paths)
    export_outputs(paths, catalog=catalog, labels=out.labels, assignments=out.assignments, formats="both", basename="export", title="ghsorter export")
    print(f"labels: {paths.labels_path} ({len(out.labels)} areas)")
    print(f"assignments: {paths.assignments_path} ({len(out.assignments)} repos)")
    print(f"output: {paths.export_dir}")
    return 0
