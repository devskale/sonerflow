from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Literal

from .store import Catalog
from .util import AppPaths, write_json_atomic


@dataclass(frozen=True)
class ExportBundle:
    areas: list[dict[str, Any]]
    assignments: list[dict[str, Any]]
    repos: list[dict[str, Any]]


def _labels_by_id(labels: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for l in labels:
        if not isinstance(l, dict):
            continue
        lid = l.get("id")
        if isinstance(lid, str) and lid:
            out[lid] = l
    return out


def _repos_by_id(catalog: Catalog) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for r in catalog.repos:
        if not isinstance(r, dict):
            continue
        rid = r.get("full_name")
        if isinstance(rid, str) and rid:
            out[rid] = r
    return out


def _render_markdown(bundle: ExportBundle, *, title: str) -> str:
    labels = _labels_by_id(bundle.areas)
    repos = {r.get("full_name"): r for r in bundle.repos if isinstance(r, dict) and isinstance(r.get("full_name"), str)}

    by_area: dict[str, list[str]] = defaultdict(list)
    uncategorized: list[str] = []

    for a in bundle.assignments:
        rid = a.get("repo_id")
        if not isinstance(rid, str) or not rid:
            continue
        cids = a.get("category_ids")
        if not isinstance(cids, list) or not cids:
            uncategorized.append(rid)
            continue
        for cid in [x for x in cids if isinstance(x, str)]:
            by_area[cid].append(rid)

    lines: list[str] = [f"# {title}", ""]
    area_items = [(cid, labels.get(cid, {}).get("name") or cid) for cid in by_area.keys()]
    area_items.sort(key=lambda x: str(x[1]).lower())

    for cid, name in area_items:
        lines.append(f"## {name}")
        desc = labels.get(cid, {}).get("description")
        if isinstance(desc, str) and desc.strip():
            lines.append(desc.strip())
            lines.append("")
        repo_list = sorted(set(by_area[cid]))
        for rid in repo_list:
            r = repos.get(rid) or {}
            url = r.get("html_url") or f"https://github.com/{rid}"
            d = r.get("description") or ""
            if isinstance(url, str) and url:
                if isinstance(d, str) and d.strip():
                    lines.append(f"- [{rid}]({url}) — {d.strip()}")
                else:
                    lines.append(f"- [{rid}]({url})")
        lines.append("")

    if uncategorized:
        lines.append("## Uncategorized")
        for rid in sorted(set(uncategorized)):
            r = repos.get(rid) or {}
            url = r.get("html_url") or f"https://github.com/{rid}"
            d = r.get("description") or ""
            if isinstance(url, str) and url:
                if isinstance(d, str) and d.strip():
                    lines.append(f"- [{rid}]({url}) — {d.strip()}")
                else:
                    lines.append(f"- [{rid}]({url})")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def export_outputs(
    paths: AppPaths,
    *,
    catalog: Catalog,
    labels: list[dict[str, Any]],
    assignments: list[dict[str, Any]],
    formats: Literal["json", "md", "both"] = "both",
    basename: str = "export",
    title: str = "ghsorter export",
) -> None:
    bundle = ExportBundle(areas=labels, assignments=assignments, repos=catalog.repos)
    if formats in ("json", "both"):
        write_json_atomic(paths.export_dir / f"{basename}.json", {"areas": labels, "assignments": assignments, "repos": catalog.repos})
    if formats in ("md", "both"):
        md = _render_markdown(bundle, title=title)
        (paths.export_dir / f"{basename}.md").write_text(md, encoding="utf-8")
