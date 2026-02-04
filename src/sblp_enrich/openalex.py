import atexit
import random
import time
from typing import Dict, List, Optional
from urllib.parse import quote

import requests

from cache_db import SqliteTableCache
from openalex_cache_keys import cache_key_for_search, cache_key_for_work

OPENALEX_WORKS = "https://api.openalex.org/works"

DB_PATH = ".openalex_cache.sqlite"

_work_cache = SqliteTableCache(DB_PATH, table="work_cache", compress=True)
_search_cache = SqliteTableCache(DB_PATH, table="search_cache", compress=True)

_PENDING = 0
_COMMIT_EVERY = 500

def _cache_commit_maybe() -> None:
    global _PENDING
    _PENDING += 1
    if _PENDING >= _COMMIT_EVERY:
        _work_cache.commit()
        _search_cache.commit()
        _PENDING = 0

def _cache_close() -> None:
    try:
        _work_cache.commit()
    except Exception:
        pass
    try:
        _search_cache.commit()
    except Exception:
        pass
    try:
        _work_cache.close()
    except Exception:
        pass
    try:
        _search_cache.close()
    except Exception:
        pass

atexit.register(_cache_close)

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

    key = cache_key_for_work(doi_iri, include_xpac=include_xpac)
    cached = _work_cache.get(key)
    if cached is not None:
        print(f"[WORK CACHE HIT] {key}", flush=True)
        return cached
    print(f"[WORK CACHE MISS] {key}", flush=True)

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
    _cache_commit_maybe()
    return data

def search_works_by_title(title: str, api_key: str, per_page: int = 5, include_xpac: bool = True) -> List[Dict]:
    key, cleaned_title = cache_key_for_search(title, per_page=per_page, include_xpac=include_xpac)
    if not cleaned_title or len(cleaned_title) < 5:
        return []

    cached = _search_cache.get(key)
    if cached is not None:
        print(f"[SEARCH CACHE HIT] {key}", flush=True)
        return cached
    print(f"[SEARCH CACHE MISS] {key}", flush=True)

    params = {
        "api_key": api_key,
        "search": cleaned_title,
        "per-page": str(per_page),
    }
    if include_xpac:
        params["include_xpac"] = "true"

    try:
        data = _http_get_json(OPENALEX_WORKS, params=params)
    except requests.HTTPError as e:
        raise

    res = data.get("results", []) or []
    if not isinstance(res, list):
        res = []
    _search_cache.set(key, res)
    _cache_commit_maybe()
    return res