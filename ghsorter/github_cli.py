from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class GhApiResult:
    payload: Any
    stderr: str


class GhError(RuntimeError):
    pass


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, check=False, text=True, capture_output=True)


def ensure_gh_auth() -> None:
    p = _run(["gh", "auth", "status"])
    if p.returncode != 0:
        raise GhError("GitHub CLI is not authenticated. Run: gh auth login")


def gh_api_json(endpoint: str, *, paginate: bool = False, slurp: bool = False, headers: dict[str, str] | None = None) -> GhApiResult:
    args = ["gh", "api", endpoint]
    if paginate:
        args.append("--paginate")
    if slurp:
        args.append("--slurp")
    if headers:
        for k, v in headers.items():
            args.extend(["-H", f"{k}:{v}"])

    p = _run(args)
    if p.returncode != 0:
        raise GhError(p.stderr.strip() or f"gh api failed for endpoint: {endpoint}")
    try:
        return GhApiResult(payload=json.loads(p.stdout or "null"), stderr=p.stderr)
    except json.JSONDecodeError as e:
        raise GhError(f"Failed to parse JSON from gh api. stderr={p.stderr.strip()}") from e


def list_starred_repos() -> list[dict[str, Any]]:
    headers = {
        "Accept": "application/vnd.github.star+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    res = gh_api_json("/user/starred?per_page=100", paginate=True, slurp=True, headers=headers)
    pages = res.payload
    if pages is None:
        return []
    if isinstance(pages, list) and (len(pages) == 0 or isinstance(pages[0], list)):
        out: list[dict[str, Any]] = []
        for page in pages:
            if isinstance(page, list):
                for x in page:
                    if not isinstance(x, dict):
                        continue
                    repo = x.get("repo")
                    if isinstance(repo, dict):
                        starred_at = x.get("starred_at")
                        if isinstance(starred_at, str) and starred_at:
                            repo = {**repo, "starred_at": starred_at}
                        out.append(repo)
                    else:
                        out.append(x)
        return out
    if isinstance(pages, list):
        out: list[dict[str, Any]] = []
        for x in pages:
            if not isinstance(x, dict):
                continue
            repo = x.get("repo")
            if isinstance(repo, dict):
                starred_at = x.get("starred_at")
                if isinstance(starred_at, str) and starred_at:
                    repo = {**repo, "starred_at": starred_at}
                out.append(repo)
            else:
                out.append(x)
        return out
    raise GhError("Unexpected response format from /user/starred")
