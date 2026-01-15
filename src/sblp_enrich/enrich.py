import time
from typing import Dict, List, Optional, Tuple

from rdflib import Graph

import dblp
import fuseki
from rdfout import ext_author_uri_from_pid, add_author_node, add_paper_author_link

def enrich_one_paper(
    endpoint_query: str,
    paper: Dict,
    sleep_s: float = 0.3,
) -> Graph:
    g = Graph()

    paper_uri = paper["paper"]
    title = paper["title"]
    year = paper.get("year")
    doi_val = paper.get("doi")

    internal_names = fuseki.fetch_internal_author_names(endpoint_query, paper_uri)

    hits = dblp.search_publications(title, h=10)
    best = dblp.pick_best_publication_hit(hits, title=title, year=year, doi=doi_val)
    time.sleep(sleep_s)
    if not best:
        return g

    info = best.get("info", {})
    authors = info.get("authors", {}).get("author", [])
    if isinstance(authors, dict):
        authors = [authors]

    for a in authors:
        author_name = (a.get("text") if isinstance(a, dict) else str(a) or "").strip()
        if not author_name:
            continue

        if author_name in internal_names:
            continue

        author_hits = dblp.search_author(author_name, h=10)
        best_author = dblp.pick_best_author_hit(author_hits, author_name)
        time.sleep(sleep_s)
        if not best_author:
            continue

        ainfo = best_author.get('info', {})
        pid_url = ainfo.get("url")
        pid_path = dblp.pid_url_to_path(pid_url or "")
        if not pid_path:
            continue

        author_uri = ext_author_uri_from_pid(pid_path)
        same_as = pid_url
        affiliations = dblp.get_affiliations(ainfo)

        author_exists = fuseki.ask_author_exists_by_sameas(endpoint_query, same_as)
        link_exists = fuseki.ask_paper_has_author_link(endpoint_query, paper_uri, str(author_uri))

        if not author_exists:
            add_author_node(
                g, 
                author_uri=author_uri, 
                name=ainfo.get("author", author_name),
                same_as=same_as,
                affiliations=affiliations,
            )

        if not link_exists:
            add_paper_author_link(g, paper_uri=paper_uri, author_uri=author_uri)

    return g

