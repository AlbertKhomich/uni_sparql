import re
import time
import random
from difflib import SequenceMatcher
from typing import Dict, List, Optional
from sblp_enrich.cache import JsonCache
from sblp_enrich.paths import default_cache_path

import requests

_publ_cache = JsonCache(default_cache_path(".dblp_publ_cache.json"))
_auth_cache = JsonCache(default_cache_path(".dblp_author_cache.json"))

DBLP_PUBL_API = "https://dblp.org/search/publ/api"
DBLP_AUTHOR_API = "https://dblp.org/search/author/api"

def _http_get_json(url: str, params: Dict, timeout: int = 20) -> Dict:
    max_tries = 8
    base = 1.0
    last = None

    for attempt in range(1, max_tries + 1):
        r = requests.get(
            url, 
            params=params,
            timeout=timeout,
            headers={"User-Agent": "uni-sparql-dblp-enricher/1.0"},
        )

        if r.status_code == 429:
            ra = r.headers.get("Retry-After")
            if ra:
                try:
                    wait = float(ra)
                except ValueError:
                    wait = base * (2 ** (attempt - 1))
            else:
                wait = base * (2 ** (attempt - 1))

            wait = wait + random.uniform(0, 0.5)
            time.sleep(min(wait, 60.0))
            continue

        if r.status_code in (500, 502, 503, 504):
            wait = base * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
            time.sleep(min(wait, 60.0))
            continue

        r.raise_for_status()
        return r.json()

    assert last is not None
    last.raise_for_status()
    return last.json()

def search_publications(title: str, h: int = 50) -> List[Dict]:
    key = f"publ::{h}::{title}"
    cached = _publ_cache.get(key)
    if cached is not None:
        return cached
    data = _http_get_json(DBLP_PUBL_API, {"q": title, "format": "json", "h": str(h)})
    hits = data.get("result", {}).get("hits", {}).get("hit", [])
    if isinstance(hits, dict):
        hits = [hits]
    _publ_cache.set(key, hits)
    _publ_cache.save()
    return hits

def search_author(name: str, h: int = 50) -> List[Dict]:
    key = f"author::{h}::{name}"
    cached = _auth_cache.get(key)
    if cached is not None:
        return cached
    data = _http_get_json(DBLP_AUTHOR_API, {"q": name, "format": "json", "h": str(h)})
    hits = data.get("result", {}).get("hits", {}).get("hit", [])
    if isinstance(hits, dict):
        hits = [hits]
    _auth_cache.set(key, hits)
    _auth_cache.save()
    return hits

def pick_best_publication_hit(hits: List[Dict], title: str, year: Optional[str], doi: Optional[str]) -> Optional[Dict]:
    doi_norm = (doi or "").strip().lower()
    if doi_norm:
        for hit in hits:
            info = hit.get("info", {})
            hdoi = (info.get("doi") or "").strip().lower()
            if hdoi and hdoi == doi_norm:
                return hit

    def sim(a: str, b: str) -> float:
        return SequenceMatcher(None, (a or "").lower().strip(), (b or "").lower().strip()).ratio()

    best = None
    best_score = -1.0
    for hit in hits:
        info = hit.get("info", {})
        ht = info.get("title", "")
        hy = str(info.get("year", "")).strip() or None
        s = sim(title, ht)
        if year and hy and year == hy:
            s += 0.05
        if s > best_score:
            best_score = s
            best = hit

    if best is None or best_score < 0.80:
        return None
    return best

def pick_best_author_hit(hits: List[Dict], name: str) -> Optional[Dict]:
    target = (name or "").lower().strip()
    best = None
    best_score = -1.0
    for hit in hits:
        info = hit.get("info", {})
        an = (info.get("author") or "").lower().strip()
        s = SequenceMatcher(None, an, target).ratio()
        if s > best_score:
            best_score = s
            best = hit
    if best is None or best_score < 0.75:
        return None
    return best

def pid_url_to_path(pid_url: str) -> Optional[str]:
    m = re.search(r"/pid/([^?#]+)$", pid_url or "")
    return m.group(1) if m else None

def get_affiliations(author_info: Dict) -> List[str]:
    notes = author_info.get("notes", {}).get("note", [])
    if isinstance(notes, dict):
        notes = [notes]
    affs = []
    for n in notes:
        if n.get("@type") == "affiliation":
            txt = (n.get("text") or "").strip()
            if txt:
                affs.append(txt)
    out, seen = [], set()
    for a in affs:
        if a not in seen:
            seen.add(a)
            out.append(a)
    return out