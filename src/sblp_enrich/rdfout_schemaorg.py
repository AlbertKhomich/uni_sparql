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

def add_identifier_doi(g: Graph, pub_uri: str, doi_value: Optional[str]) -> None:
    if not doi_value:
        return
    s = URIRef(pub_uri)

    v = (doi_value or "").strip()
    if not v:
        return

    vl = v.lower()
    if vl.startswith("https://doi.org/"):
        v = v.split("doi.org/", 1)[1].lstrip()
    elif vl.startswith("http://doi.org/"):
        v = v.split("doi.org/", 1)[1].lstrip()
    elif vl.startswith("doi:"):
        v = v.split(":", 1)[1].strip()

    v = v.strip()
    if not v:
        return

    g.add((s, SCHEMA.identifier, Literal(f"DOI:{v}")))
