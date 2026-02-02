from __future__ import annotations

import re
from typing import Tuple

_BAD_CHARS_RE = re.compile(r"[|]")
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")

def clean_search(s: str, max_len: int = 300) -> str:
    s = (s or "").strip()
    s = _CTRL_RE.sub(" ", s)
    s = _BAD_CHARS_RE.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:max_len]

def cache_key_for_work(doi_iri: str, include_xpac: bool) -> str:
    return f"work::{doi_iri}::xpac={include_xpac}"

def cache_key_for_search(title: str, per_page: int, include_xpac: bool) -> Tuple[str, str]:
    cleaned = clean_search(title)
    return f"search::{per_page}::xpac={include_xpac}::{cleaned}", cleaned