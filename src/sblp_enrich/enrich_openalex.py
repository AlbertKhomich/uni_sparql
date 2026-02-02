import time
from typing import Dict, List, Optional

from rdflib import Graph

import openalex
import fuseki_schemaorg as fuseki
from rdfout_schemaorg import add_affiliations, add_sameas_openalex, add_sameas_doi
from author_match import pick_best_authorship, pick_best_work_by_title
from openalex_utils import doi_from_work


def enrich_one_publication_openalex(
    endpoint_query: str,
    pub_row: Dict,
    api_key: str,
    sleep_s: float = 0.8,
) -> Graph:
    g = Graph()

    pub_uri = pub_row["pub"]
    title = pub_row.get("title") or ""
    doi_iri = pub_row.get("doi")

    authors = fuseki.fetch_pub_authors(endpoint_query, pub_uri)

    work = None
    if doi_iri:
        work = openalex.get_work_by_doi(doi_iri, api_key=api_key, include_xpac=True)
        time.sleep(sleep_s)

    if not work and title:
        results = openalex.search_works_by_title(title, api_key=api_key, per_page=5, include_xpac=True)
        time.sleep(sleep_s)
        work = pick_best_work_by_title(title, results)

    if not work:
        return g

    authorships = work.get("authorships", []) or []
    if not isinstance(authorships, list) or not authorships:
        return g

    if not doi_iri:
        doi2 = doi_from_work(work)
        if doi2:
            add_sameas_doi(g, pub_uri, doi2)

    for p in authors:
        person_uri = p["person"]
        person_name = p.get("name") or ""

        if not person_name:
            continue

        au = pick_best_authorship(authorships, person_name)
        if not au:
            continue

        openalex_author_id = (au.get("author") or {}).get("id")

        affs = []
        for aff in (au.get("affiliations") or []):
            ras = (aff.get("raw_affiliation_string") or "").strip()
            if ras:
                affs.append(ras)

        seen = set()
        affs2 = []
        for a in affs:
            if a not in seen:
                seen.add(a)
                affs2.append(a)

        if affs2:
            add_affiliations(g, person_uri, affs2)
        if openalex_author_id:
            add_sameas_openalex(g, person_uri, openalex_author_id)

    return g