from typing import Dict, List, Optional, Set, Tuple
import requests

SCHEMA = "https://schema.dice-research.org/"

def sparql_select(endpoint_query: str, sparql: str) -> List[Dict]:
    r = requests.post(
        endpoint_query,
        data={"query": sparql},
        headers={"Accept": "application/sparql-results+json"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json().get("results", {}).get("bindings", [])

def sparql_ask(endpoint_query: str, sparql: str) -> bool:
    r = requests.post(
        endpoint_query,
        data={"query": sparql},
        headers={"Accept": "application/sparql-results+json"},
        timeout=20,
    )
    r.raise_for_status()
    return bool(r.json().get("boolean", False))

def sparql_update(endpoint_update: str, sparql_update: str) -> None:
    r = requests.post(endpoint_update, data={"update": sparql_update}, timeout=30)
    r.raise_for_status()

def fetch_papers(endpoint_query: str, limit: int = 0) -> List[Dict]:
    q = f"""
    SELECT ?paper ?title ?year ?doi WHERE {{
        ?paper a <{SCHEMA}Publication> ;
            <{SCHEMA}title> ?title .
        OPTIONAL {{ ?paper <{SCHEMA}year> ?year . }}
        OPTIONAL {{ ?paper <{SCHEMA}doi> ?doi . }}
    }}
    """.strip()
    rows = sparql_select(endpoint_query, q)
    if limit and limit > 0:
        rows = rows[:limit]
    out = []
    for b in rows:
        out.append({
            "paper": b["paper"]["value"],
            "title": b["title"]["value"],
            "year": b.get("year", {}).get("value"),
            "doi": b.get("doi", {}).get("value"),
        })
    return out

def fetch_internal_author_names(endpoint_query: str, paper_uri: str) -> Set[str]:
    q = f"""
    SELECT ?name WHERE {{
        <{paper_uri}> <{SCHEMA}authorName> ?name .
    }}
    """.strip()
    rows = sparql_select(endpoint_query, q)
    return {b["name"]["value"] for b in rows}

def ask_author_exists_by_sameas(endpoint_query: str, same_as: str) -> bool:
    q = f"ASK WHERE {{ ?a <{SCHEMA}sameAs> <{same_as}> . }}".strip()
    return sparql_ask(endpoint_query, q)

def ask_paper_has_author_link(endpoint_query: str, paper_uri: str, author_uri: str) -> bool:
    q = f"ASK WHERE {{ <{paper_uri}> <{SCHEMA}author> <{author_uri}> . }}".strip()
    return sparql_ask(endpoint_query, q)