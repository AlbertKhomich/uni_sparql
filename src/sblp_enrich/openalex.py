import random
import time
import re
from typing import Dict, List, Optional
from urllib.parse import quote

import requests
from cache import JsonCache

OPENALEX_WORKS = "https://api.openalex.org/works"

_work_cache = JsonCache(".openalex_work_cache.json")
_search_cache = JsonCache(".openalex_search_cache.json")
_BAD_CHARS_RE = re.compile(r"[|]")
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")

def _clean_search(s: str, max_len: int = 300) -> str:
    s = (s or "").strip()
    s = _CTRL_RE.sub(" ", s)
    s = _BAD_CHARS_RE.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:max_len]

def _http_get_json(url: str, params: Dict, timeout: int = 60) -> Dict:
    max_tries = 8
    base = 1.0
    last = None
    last_exc = None

    for attempt in range(1, max_tries + 1):
        try:
            r = requests.get(
                url,
                params=params,
                timeout=(10, timeout),
                headers={"User-Agent": "uni-sparql-openalex-enricher/1.0"},
            )
            last = r

            if r.status_code == 429:
                ra = r.headers.get("Retry-After")
                if ra: 
                    try:
                        wait = float(ra)
                    except ValueError:
                        wait = base * (2 ** (attempt - 1))
                else:
                    wait = base * (2 ** (attempt - 1))
                time.sleep(min(wait + random.uniform(0, 0.5), 60.0))
                continue

            if r.status_code in (408, 500, 502, 503, 504):
                wait = base * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
                time.sleep(min(wait, 60.0))
                continue

            r.raise_for_status()
            return r.json()

        except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectTimeout, requests.exceptions.ConnectionError) as e:
            last_exc = e
            wait = base * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
            time.sleep(min(wait, 60.0))
            continue
    
    if last_exc is not None:
        raise last_exc
    if last is not None:
        last.raise_for_status()
    raise RuntimeError("OpenAlex request failed: no HTTP response and no captured exception")

def get_work_by_doi(doi_iri: str, api_key: str, include_xpac: bool = True) -> Optional[Dict]:
    doi_iri = (doi_iri or "").strip()
    if not doi_iri:
        return None

    key = f"work::{doi_iri}::xpac={include_xpac}"
    cached = _work_cache.get(key)
    if cached is not None:
        return cached

    params = {"api_key": api_key}
    if include_xpac:
        params["include_xpac"] = "true"

    try:
        data = _http_get_json(f"{OPENALEX_WORKS}/{quote(doi_iri, safe=':/')}", params=params)
    except requests.HTTPError as e:
        if getattr(e.response, "status_code", None) == 404:
            return None
        raise
    _work_cache.set(key, data)
    _work_cache.save()
    return data

def search_works_by_title(title: str, api_key: str, per_page: int = 5, include_xpac: bool = True) -> List[Dict]:
    title = _clean_search(title)
    if not title or len(title) < 5:
        return []

    key = f"search::{per_page}::xpac={include_xpac}::{title}"
    cached = _search_cache.get(key)
    if cached is not None:
        return cached

    params = {
        "api_key": api_key,
        "search": title,
        "per-page": str(per_page),
    }
    if include_xpac:
        params["include_xpac"] = "true"

    try:
        data = _http_get_json(OPENALEX_WORKS, params=params)
    except requests.HTTPError as e:
        if getattr(e.response, "status_code", None) == 400:
            _search_cache.set(key, [])
            _search_cache.save()
            return []
        raise

    res = data.get("results", []) or []
    if not isinstance(res, list):
        res = []
    _search_cache.set(key, res)
    _search_cache.save()
    return res