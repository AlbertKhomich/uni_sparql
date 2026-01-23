import random
import time
from typing import Dict, List, Optional

import requests
from cache import JsonCache

OPENALEX_WORKS = "https://api.openalex.org/works"

_work_cache = JsonCache(".openalex_work_cache.json")
_search_cache = JsonCache(".openalex_search_cache.json")

def _http_get_json(url: str, params: Dict, timeout: int = 25) -> Dict:
    max_tries = 8
    base = 1.0
    last = None

    for attempt in range(1, max_tries + 1):
        r = requests.get(
            url,
            params=params,
            timeout=timeout,
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

        if r.status_code in (500, 502, 503, 504):
            wait = base * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
            time.sleep(min(wait, 60.0))
            continue

        r.raise_for_status()
        return r.json()

    assert last is not None
    last.raise_for_status()
    return last.json()

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

    data = _http_get_json(f"{OPENALEX_WORKS}/{doi_iri}", params=params)
    _work_cache.set(key, data)
    _work_cache.save()
    return data

def search_works_by_title(title: str, api_key: str, per_page: int = 5, include_xpac: bool = True) -> List[Dict]:
    title = (title or "").strip()
    if not title:
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

    data = _http_get_json(OPENALEX_WORKS, params=params)
    res = data.get("results", []) or []
    if not isinstance(res, list):
        res = []
    _search_cache.set(key, res)
    _search_cache.save()
    return res