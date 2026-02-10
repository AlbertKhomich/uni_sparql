import time
import atexit
import re
import requests
from typing import Dict, List, Optional

from rdflib import Graph

import openalex
import fuseki_schemaorg as fuseki
from cache_db import SqliteTableCache
from rdfout_schemaorg import add_affiliation, add_sameas, add_identifier_doi
from author_match import pick_best_authorship, pick_best_work_by_title
from openalex_utils import doi_from_work


_DOI_RE = re.compile(r"^DOI:\s*(10\.\d{4,9}/\S+)\s*$", re.IGNORECASE)

def openalex_author_endpoint(author_id: str) -> Optional[str]:
    if not author_id:
        return None
    s = author_id.strip().strip("/")
    tail = s.rsplit("/", 1)[-1]
    return f"https://api.openalex.org/authors/{tail}"

def doi_identifier_to_url(identifier: str) -> Optional[str]:
    s = (identifier or "").strip()
    m = _DOI_RE.match(s)
    if not m:
        return None
    doi = m.group(1).rstrip(").],.;")
    return f"https://doi.org/{doi}"

def enrich_one_publication_openalex(
    endpoint_query: str,
    pub_row: Dict,
    api_key: str,
    sleep_s: float = 0.8,
) -> Graph:
    g = Graph()

    pub_uri = pub_row["pub"]
    title = pub_row.get("title") or ""
    doi_iri = pub_row.get("doi_ident")
    doi_url = doi_identifier_to_url(doi_iri)

    authors = fuseki.fetch_pub_authors(endpoint_query, pub_uri)
    work = None
    if doi_url:
        work = openalex.get_work_by_doi(doi_url, api_key=api_key, include_xpac=True)

    if not work and title:
        results = openalex.search_works_by_title(title, api_key=api_key, per_page=5, include_xpac=True)
        work = pick_best_work_by_title(title, results)

    if not work:
        return g

    authorships = work.get("authorships", []) or []
    if not isinstance(authorships, list) or not authorships:
        return g

    if not doi_url:
        doi2 = doi_from_work(work)
        if doi2:
            add_identifier_doi(g, pub_uri, doi2)

    for p in authors:
        person_uri = p["person"]
        person_name = p.get("name") or ""

        if not person_name:
            continue

        au = pick_best_authorship(authorships, person_name)
        if not au:
            continue
        openalex_author_id = (au.get("author") or {}).get("id")
        if not openalex_author_id:
            continue

        au_expanded = openalex.authors_cache.get(openalex_author_id)
        
        if au_expanded is None:
            print(f"[NO CACHE FOR] {openalex_author_id}")

            author_endpoint = openalex_author_endpoint(openalex_author_id)
            add_sameas(g, person_uri, author_endpoint)

            params = {"api_key": api_key}
            try:
                au_expanded = openalex.http_get_json(author_endpoint, params=params)
            except requests.HTTPError as e:
                status = getattr(e.response, "status_code", None)
                if status == 404:
                    print("OpenAlex author not found -> skipping", author_endpoint)
                    continue

                raise

            openalex.authors_cache.set(openalex_author_id, au_expanded)
            openalex.cache_commit_maybe()

        orcid = (au_expanded.get("orcid") or "").strip()
        add_sameas(g, person_uri, orcid)
        
        affs: List[Dict[str, str]] = []

        for aff in (au_expanded.get("affiliations") or []):
            inst = (aff or {}).get("institution") or {}
            add_affiliation(g, person_uri, inst)

    return g