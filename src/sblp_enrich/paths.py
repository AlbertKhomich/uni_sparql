from __future__ import annotations

import os
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
SRC_ROOT = PACKAGE_ROOT.parent
REPO_ROOT = SRC_ROOT.parent


def runtime_root(create: bool = True) -> Path:
    env = os.getenv("SBLP_ENRICH_RUNTIME_DIR")
    path = Path(env).expanduser() if env else REPO_ROOT / "var" / "sblp_enrich"
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def cache_dir(create: bool = True) -> Path:
    env = os.getenv("SBLP_ENRICH_CACHE_DIR")
    path = Path(env).expanduser() if env else runtime_root(create=create) / "cache"
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def default_cache_path(filename: str) -> str:
    env_dir = os.getenv("SBLP_ENRICH_CACHE_DIR")
    if env_dir:
        return str(cache_dir() / filename)

    legacy_path = PACKAGE_ROOT / filename
    if legacy_path.exists():
        return str(legacy_path)

    return str(cache_dir() / filename)
