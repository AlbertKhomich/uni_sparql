import time
import atexit
import re
import requests
from typing import Dict, List, Optional, Set

from rdflib import Graph

import openalex
import fuseki_schemaorg as fuseki
from cache_db import SqliteTableCache
from rdfout_schemaorg import (
    add_affiliation,
    add_sameas,
    add_identifier_doi,
    add_publication_about_topic,
    add_publication_pdf_url,
)
from author_match import pick_best_authorship, pick_best_work_by_title
from openalex_utils import doi_from_work


_DOI_RE = re.compile(r"^DOI:\s*(10\.\d{4,9}/\S+)\s*$", re.IGNORECASE)
_OA_FIELD_SUBFIELD_RE = re.compile(r"^https?://openalex\.org/(fields|subfields)/[0-9]+/?$", re.IGNORECASE)

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

def _normalize_openalex_field_or_subfield_id(topic_uri: str) -> Optional[str]:
    t = (topic_uri or "").strip()
    if not t:
        return None
    if not _OA_FIELD_SUBFIELD_RE.match(t):
        return None
    t = t.rstrip("/")
    t = "https://openalex.org/" + t.split("openalex.org/", 1)[1]
    return t

def _collect_work_field_and_subfield_ids(work: Dict) -> Set[str]:
    out: Set[str] = set()

    def push(topic_uri: Optional[str]) -> None:
        norm = _normalize_openalex_field_or_subfield_id(topic_uri or "")
        if norm:
            out.add(norm)

    primary_topic = work.get("primary_topic") or {}
    if isinstance(primary_topic, dict):
        sf = primary_topic.get("subfield") or {}
        if isinstance(sf, dict):
            push(sf.get("id"))
        f = primary_topic.get("field") or {}
        if isinstance(f, dict):
            push(f.get("id"))

    topics = work.get("topics") or []
    if isinstance(topics, list):
        for t in topics:
            if not isinstance(t, dict):
                continue
            sf = t.get("subfield") or {}
            if isinstance(sf, dict):
                push(sf.get("id"))
            f = t.get("field") or {}
            if isinstance(f, dict):
                push(f.get("id"))

    return out

def _extract_primary_location_pdf_url(work: Dict) -> Optional[str]:
    primary_location = work.get("primary_location") or {}
    if not isinstance(primary_location, dict):
        return None

    pdf_url = (primary_location.get("pdf_url") or "").strip()
    if not pdf_url:
        return None
    return pdf_url

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
    doi_cache_hit = False
    title_cache_hit = False

    if doi_url:
        work = openalex.get_cached_work_by_doi(doi_url, include_xpac=True)
        doi_cache_hit = work is not None

    if not work and title:
        cached_results = openalex.get_cached_search_works_by_title(
            title, per_page=5, include_xpac=True
        )
        title_cache_hit = cached_results is not None
        if cached_results is not None:
            work = pick_best_work_by_title(title, cached_results)

    if not work and not doi_cache_hit and not title_cache_hit:
        if doi_url:
            work = openalex.get_work_by_doi(doi_url, api_key=api_key, include_xpac=True)
        if not work and title:
            results = openalex.search_works_by_title(
                title, api_key=api_key, per_page=5, include_xpac=True
            )
            work = pick_best_work_by_title(title, results)

    if not work:
        return g

    pdf_url = _extract_primary_location_pdf_url(work)
    if pdf_url:
        add_publication_pdf_url(g, pub_uri, pdf_url)

    topic_ids = _collect_work_field_and_subfield_ids(work)
    if topic_ids:
        existing_topic_ids = fuseki.fetch_pub_openalex_field_subfield_links(endpoint_query, pub_uri)
        for topic_id in topic_ids:
            if topic_id in existing_topic_ids:
                continue
            add_publication_about_topic(g, pub_uri, topic_id)

    authorships = work.get("authorships", []) or []
    if not isinstance(authorships, list):
        authorships = []

    if not doi_url:
        doi2 = doi_from_work(work)
        if doi2:
            add_identifier_doi(g, pub_uri, doi2)

    for p in authors:
        person_uri = p["person"]
        person_name = p.get("name") or ""
        has_affiliation = bool(p.get("has_affiliation"))

        if not person_name:
            continue
        if has_affiliation:
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
        add_sameas(g, person_uri, openalex_author_id)
        
        affs: List[Dict[str, str]] = []

        insts = au_expanded.get("last_known_institutions") or []
        if insts:
            add_affiliation(g, person_uri, insts[0])

        if not insts:
            insts2 = au_expanded.get("affiliations") or []
            if insts2:
                add_affiliation(g, person_uri, insts2[0].get("institution"))

    return g
