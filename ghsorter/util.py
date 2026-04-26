from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AppPaths:
    root: Path
    config_path: Path
    catalog_path: Path
    labels_path: Path
    assignments_path: Path
    steering_path: Path
    runs_dir: Path
    export_dir: Path


def default_root_dir() -> Path:
    env = os.environ.get("GHSORTER_HOME")
    if env:
        return Path(env).expanduser()
    if (Path.cwd() / ".ghsorter.json").exists():
        return Path.cwd() / ".ghsorter_store"
    return Path.home() / ".ghsorter"


def app_paths(root: Path) -> AppPaths:
    root = root.expanduser()
    return AppPaths(
        root=root,
        config_path=root / "config.json",
        catalog_path=root / "catalog.json",
        labels_path=root / "labels.json",
        assignments_path=root / "assignments.json",
        steering_path=root / "steering.json",
        runs_dir=root / "runs",
        export_dir=root / "output",
    )


def ensure_dirs(paths: AppPaths) -> None:
    paths.root.mkdir(parents=True, exist_ok=True)
    paths.runs_dir.mkdir(parents=True, exist_ok=True)
    paths.export_dir.mkdir(parents=True, exist_ok=True)


def read_json(path: Path) -> Any:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=str(path.parent)) as tmp:
            tmp.write(payload)
            tmp_path = Path(tmp.name)
        tmp_path.replace(path)
    except PermissionError:
        with path.open("w", encoding="utf-8") as f:
            f.write(payload)
