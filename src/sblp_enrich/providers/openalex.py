import atexit
import random
import re
import time
from typing import Dict, List, Optional
from urllib.parse import quote

import requests

from sblp_enrich.cache_db import SqliteTableCache
from sblp_enrich.paths import default_cache_path
from sblp_enrich.providers.openalex_cache_keys import cache_key_for_search, cache_key_for_work

OPENALEX_WORKS = "https://api.openalex.org/works"
OPENALEX_INSTITUTIONS = "https://api.openalex.org/institutions"

DB_PATH = default_cache_path(".openalex_cache.sqlite")

_work_cache = SqliteTableCache(DB_PATH, table="work_cache", compress=True)
_search_cache = SqliteTableCache(DB_PATH, table="search_cache", compress=True)
authors_cache = SqliteTableCache(DB_PATH, table="authors_cache", compress=True)
_affiliation_geo_cache = SqliteTableCache(DB_PATH, table="affiliation_geo_cache", compress=True)

_PENDING = 0
_COMMIT_EVERY = 500
_OPENALEX_INST_ID_RE = re.compile(r"^I[0-9]+$", re.IGNORECASE)

def cache_commit_maybe() -> None:
    global _PENDING
    _PENDING += 1
    if _PENDING >= _COMMIT_EVERY:
        _work_cache.commit()
        _search_cache.commit()
        authors_cache.commit()
        _affiliation_geo_cache.commit()
        _PENDING = 0

def cache_close() -> None:
    try:
        _work_cache.commit()
    except Exception:
        pass
    try:
        _search_cache.commit()
    except Exception:
        pass
    try:
        authors_cache.commit()
    except Exception:
        pass
    try:
        _affiliation_geo_cache.commit()
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
    try:
        authors_cache.close()
    except Exception:
        pass
    try:
        _affiliation_geo_cache.close()
    except Exception:
        pass


atexit.register(cache_close)

def http_get_json(url: str, params: Dict, timeout: int = 60) -> Dict:
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
    cached = get_cached_work_by_doi(doi_iri, include_xpac=include_xpac)
    if cached is not None:
        return cached

    params = {"api_key": api_key}
    if include_xpac:
        params["include_xpac"] = "true"

    try:
        data = http_get_json(f"{OPENALEX_WORKS}/{quote(doi_iri, safe=':/')}", params=params)
    except requests.HTTPError as e:
        if getattr(e.response, "status_code", None) == 404:
            return None
        raise
    _work_cache.set(key, data)
    cache_commit_maybe()
    return data

def get_cached_work_by_doi(doi_iri: str, include_xpac: bool = True) -> Optional[Dict]:
    doi_iri = (doi_iri or "").strip()
    if not doi_iri:
        return None

    key = cache_key_for_work(doi_iri, include_xpac=include_xpac)
    cached = _work_cache.get(key)
    if cached is not None:
        print(f"[WORK CACHE HIT] {key}", flush=True)
        return cached

    print(f"[WORK CACHE MISS] {key}", flush=True)
    return None

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
        data = http_get_json(OPENALEX_WORKS, params=params)
    except requests.HTTPError as e:
        raise

    res = data.get("results", []) or []
    if not isinstance(res, list):
        res = []
    _search_cache.set(key, res)
    cache_commit_maybe()
    return res

def get_cached_search_works_by_title(
    title: str, per_page: int = 5, include_xpac: bool = True
) -> Optional[List[Dict]]:
    key, cleaned_title = cache_key_for_search(title, per_page=per_page, include_xpac=include_xpac)
    if not cleaned_title or len(cleaned_title) < 5:
        return None

    cached = _search_cache.get(key)
    if cached is not None:
        print(f"[SEARCH CACHE HIT] {key}", flush=True)
        if isinstance(cached, list):
            return cached
        return []

    print(f"[SEARCH CACHE MISS] {key}", flush=True)
    return None

def normalize_openalex_institution_id(openalex_id: str) -> Optional[str]:
    s = (openalex_id or "").strip()
    if not s:
        return None

    s = s.rstrip("/")
    tail = s.rsplit("/", 1)[-1]
    if not _OPENALEX_INST_ID_RE.match(tail):
        return None
    return f"https://openalex.org/{tail.upper()}"

def get_institution_by_openalex_id(openalex_id: str, api_key: str) -> Optional[Dict]:
    norm = normalize_openalex_institution_id(openalex_id)
    if not norm:
        return None

    tail = norm.rsplit("/", 1)[-1]
    params = {"api_key": api_key}

    try:
        return http_get_json(f"{OPENALEX_INSTITUTIONS}/{tail}", params=params)
    except requests.HTTPError as e:
        if getattr(e.response, "status_code", None) == 404:
            return None
        raise

def get_cached_affiliation_geo_payload(affiliation_uri: str) -> Optional[Dict]:
    key = (affiliation_uri or "").strip()
    if not key:
        return None

    cached = _affiliation_geo_cache.get(key)
    if cached is not None:
        print(f"[AFF GEO CACHE HIT] {key}", flush=True)
        if isinstance(cached, dict):
            return cached
        return {}

    print(f"[AFF GEO CACHE MISS] {key}", flush=True)
    return None

def set_cached_affiliation_geo_payload(affiliation_uri: str, payload: Dict) -> None:
    key = (affiliation_uri or "").strip()
    if not key:
        return
    _affiliation_geo_cache.set(key, payload)
    cache_commit_maybe()
