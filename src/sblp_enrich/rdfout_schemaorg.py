from typing import Iterable, Optional
from rdflib import Graph, URIRef, Literal, Namespace

SCHEMA = Namespace("https://schema.org/")

def add_affiliations(g: Graph, person_uri: str, affiliations: Iterable[str]) -> None:
    s = URIRef(person_uri)
    for aff in affiliations:
        aff = (aff or "").strip()
        if aff:
            g.add((s, SCHEMA.affiliation, Literal(aff)))

def add_sameas_openalex(g: Graph, person_uri: str, openalex_author_id: Optional[str]) -> None:
    if not openalex_author_id:
        return
    s = URIRef(person_uri)
    g.add((s, SCHEMA.sameAs, URIRef(openalex_author_id)))
