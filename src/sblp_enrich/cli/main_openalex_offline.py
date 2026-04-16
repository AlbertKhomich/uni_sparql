from __future__ import annotations

import argparse
import json
from typing import Any, Dict, Iterable, List, Optional

from rdflib import Graph

from sblp_enrich.providers import fuseki_schemaorg as fuseki
from sblp_enrich.rdf.rdfout_schemaorg import add_affiliations, add_sameas_openalex, add_identifier_doi
from sblp_enrich.author_match import pick_best_authorship, pick_best_work_by_title
from sblp_enrich.providers.openalex_utils import doi_from_work, normalize_doi_iri
from sblp_enrich.providers.openalex_cache_keys import cache_key_for_work, cache_key_for_search
from sblp_enrich.paths import default_cache_path

_DEFAULT_WORK_CACHE = default_cache_path(".openalex_work_cache.json")
_DEFAULT_SEARCH_CACHE = default_cache_path(".openalex_search_cache.json")
INCLUDE_XPAC = True

def _load_json_object(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Cache file is not a JSON object: {path}")
    return data

def _iter_publications(endpoint_query: str, batch_size: int, max_pubs: int) -> Iterable[Dict[str, Any]]:
    offset = 0
    yielded = 0

    while True:
        lim = batch_size
        if max_pubs and max_pubs > 0:
            remaining = max_pubs - yielded
            if remaining <= 0:
                return
            lim = min(lim, remaining)

        pubs = fuseki.fetch_publications(endpoint_query, limit=lim, offset=offset)
        if not pubs:
            return

        for p in pubs:
            yield p 
            yielded += 1

        offset += len(pubs)

def _write_graph_as_nt(g: Graph, fp) -> int:
    n = 0
    for s, p, o in g:
        fp.write(f"{s.n3()} {p.n3()} {o.n3()} .\n")
        n += 1
    return n

def _get_cached_work(work_cache: Dict[str, Any], doi_iri: str, include_xpac: bool) -> Optional[Dict[str, Any]]:
    doi_norm = normalize_doi_iri(doi_iri)
    if not doi_norm:
        return None

    variants = [doi_norm]
    if doi_norm.startswith("https://doi.org/"):
        variants.append("http://doi.org/" + doi_norm.split("doi.org/", 1)[1])

    for d in variants:
        key = cache_key_for_work(d, include_xpac=include_xpac)
        w = work_cache.get(key)
        if isinstance(w, dict):
            return w

    return None

def _get_cached_search_results(
    search_cache: Dict[str, Any], title: str, include_xpac: bool, per_page_candidates: Iterable[int] = (5, 10, 3)
) -> List[Dict[str, Any]]:
    for per_page in per_page_candidates:
        key, _cleaned = cache_key_for_search(title, per_page=per_page, include_xpac=include_xpac)
        if not _cleaned or len(_cleaned) < 5:
            continue
        res = search_cache.get(key)
        if isinstance(res, list):
            return [r for r in res if isinstance(r, dict)]

    return []

def main() -> None:
    ap = argparse.ArgumentParser(
        description=(
            "Generate an .nt from cache with missing papers doi, authors affiliations and author sameAs to openalex"
        )
    )
    ap.add_argument("--fuseki-query", required=True)
    ap.add_argument("--out-nt", required=True)
    ap.add_argument("--work-cache", default=_DEFAULT_WORK_CACHE)
    ap.add_argument("--search-cache", default=_DEFAULT_SEARCH_CACHE)
    ap.add_argument("--batch-size", type=int, default=500)
    args = ap.parse_args()

    work_cache = _load_json_object(args.work_cache)
    search_cache = _load_json_object(args.search_cache)
    
    pubs_total = 0
    pubs_with_cached_work = 0
    pubs_added_doi = 0
    authors_enriched = 0
    triples_written = 0

    with open(args.out_nt, "w", encoding="utf-8") as out:
        for pub in _iter_publications(args.fuseki_query, batch_size=args.batch_size, max_pubs=0):
            pubs_total += 1

            pub_iri = pub["pub"]
            title = pub.get("title") or ""
            doi_sameas = pub.get("doi_sameas")
            doi_ident = pub.get("doi_ident")

            doi_src = doi_sameas or doi_ident

            work = None
            if doi_src:
                work = _get_cached_work(work_cache, doi_src, include_xpac=INCLUDE_XPAC)

            if not work and title and search_cache is not None:
                results = _get_cached_search_results(search_cache, title, include_xpac=INCLUDE_XPAC)
                work = pick_best_work_by_title(title, results)

            if not work:
                continue

            pubs_with_cached_work += 1

            g = Graph()

            if not doi_ident:
                doi2 = doi_from_work(work)
                if doi2:
                    add_identifier_doi(g, pub_iri, doi2)
                    pubs_added_doi += 1

            authorships = work.get("authorships", []) or []
            if isinstance(authorships, list) and authorships:
                authors = fuseki.fetch_pub_authors(args.fuseki_query, pub_iri)

                for p in authors:
                    person_uri = p["person"]
                    person_name = p.get("name") or ""
                    if not person_name:
                        continue

                    au = pick_best_authorship(authorships, person_name)
                    if not au:
                        continue

                    openalex_author_id = (au.get("author") or {}).get("id")

                    affs: List[str] = []
                    for aff in (au.get("affiliations") or []):
                        ras = (aff.get("raw_affiliation_string") or "").strip()
                        if ras:
                            affs.append(ras)

                    seen = set()
                    affs2: List[str] = []
                    for a in affs:
                        if a not in seen:
                            seen.add(a)
                            affs2.append(a)

                    wrote_any = False
                    if affs2:
                        add_affiliations(g, person_uri, affs2)
                        wrote_any = True
                    if openalex_author_id:
                        add_sameas_openalex(g, person_uri, openalex_author_id)
                        wrote_any = True

                    if wrote_any:
                        authors_enriched += 1

            triples_written += _write_graph_as_nt(g, out)

    print(f"Processed publications: {pubs_total}")
    print(f"With cached OpenAlex work: {pubs_with_cached_work}")
    print(f"Publications where DOI was added: {pubs_added_doi}")
    print(f"Author records enriched: {authors_enriched}")
    print(f"Triples written: {triples_written}")
    print(f"Wrote: {args.out_nt}")

if __name__ == "__main__":
    main()

    



