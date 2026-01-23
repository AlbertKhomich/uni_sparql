import re
import time
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Tuple

from rdflib import Graph

import openalex
import fuseki_schemaorg as fuseki
from rdfout_schemaorg import add_affiliations, add_sameas_openalex

def _norm_name(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s

def _last_and_initial(s: str) -> Tuple[str, str]:
    s = _norm_name(s)
    if "," in s:
        last, rest = [p.strip() for p in s.split(",", 1)]
        ini = (rest[:1] if rest else "")
        return last, ini
    parts = s.split()
    if not parts:
        return "", ""
    last = parts[-1]
    ini = parts[0][:1] if parts[0] else ""
    return last, ini

def _sim(a: str, b: str) -> float:
    return SequenceMatcher(None, _norm_name(a), _norm_name(b)).ratio()

def _pick_best_authorship(authorships: List[Dict], person_name: str) -> Optional[Dict]:
    pl, pi = _last_and_initial(person_name)

    best = None
    best_score = -1.0

    for au in authorships:
        author = (au.get("author") or {})
        dn = author.get("display_name") or ""
        al, ai = _last_and_initial(dn)

        score = 0.0
        if pl and al and pl == al:
            score += 0.6
            if pi and ai and pi == ai:
                score += 0.2

        score += 0.2 * _sim(person_name, dn)

        if score > best_score:
            best_score = score
            best = au

    if best is None or best_score < 0.55:
        return None
    return best

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
        best = None
        best_s = -1.0
        for r in results:
            s = _sim(title, r.get("display_name") or r.get("title") or "")
            if s > best_s:
                best_s = s
                best = r
        if best and best_s >= 0.80:
            work = best

    if not work:
        return g

    authorships = work.get("authorships", []) or []
    if not isinstance(authorships, list) or not authorships:
        return g

    for p in authors:
        person_uri = p["person"]
        person_name = p.get("name") or ""

        if not person_name:
            continue

        au = _pick_best_authorship(authorships, person_name)
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