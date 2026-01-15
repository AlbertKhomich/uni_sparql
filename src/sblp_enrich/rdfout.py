from typing import List
from rdflib import Graph, URIRef, Literal, Namespace
from rdflib.namespace import RDF

SCHEMA = Namespace("https://schema.dice-research.org/")

def ext_author_uri_from_pid(pid_path: str) -> URIRef:
    pid_safe = pid_path.strip().replace("/", "_")
    return URIRef(f"https://dice-research.org/external/dblp/pid/{pid_safe}")

def add_author_node(g: Graph, author_uri: URIRef, name: str, same_as: str, affiliations: List[str]) -> None:
    g.add((author_uri, RDF.type, URIRef(f"{SCHEMA}Person")))
    g.add((author_uri, URIRef(f"{SCHEMA}name"), Literal(name)))
    g.add((author_uri, URIRef(f"{SCHEMA}sameAs"), URIRef(same_as)))
    for aff in affiliations:
        g.add((author_uri, URIRef(f"{SCHEMA}affiliation"), Literal(aff)))

def add_paper_author_link(g: Graph, paper_uri: str, author_uri: URIRef) -> None:
    g.add((URIRef(paper_uri), URIRef(f"{SCHEMA}author"), author_uri))

def serialize_nt(g: Graph, path: str) -> None:
    g.serialize(destination=path, format="nt")

def make_insert_update_from_graph(g: Graph) -> str:
    nt = g.serialize(format="nt")
    return "INSERT DATA {\n" + nt + "}\n"