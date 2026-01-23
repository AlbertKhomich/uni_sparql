from typing import Dict, List, Optional
import requests

SCHEMA = "https://schema.org/"

def sparql_select(endpoint_query: str, sparql: str) -> List[Dict]:
    r = requests.post(
        endpoint_query,
        data={"query": sparql},
        headers={"Accept": "application/sparql-results+json"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json().get("results", {}).get("bindings", [])

def _is_doi_iri(u: Optional[str]) -> bool:
    if not u:
        return False
    s = u.strip().lower()
    return s.startswith("https://doi.org/") or s.startswith("http://doi.org/")

def fetch_publications(endpoint_query: str, limit: int = 0) -> List[Dict]:
    q = f"""
    select ?pub ?title ?year ?sameAs where {{
        ?pub <{SCHEMA}name> ?title .
        optional {{ ?pub <{SCHEMA}datePublished> ?year . }}
        optional {{ ?pub <{SCHEMA}sameAs> ?sameAs . }}
    }}
    """.strip()

    rows = sparql_select(endpoint_query, q)

    by_pub: Dict[str, Dict] = {}

    for b in rows:
        pub = b["pub"]["value"]
        title = b.get("title", {}).get("value")
        year = b.get("year", {}).get("value")
        same_as = b.get("sameAs", {}).get("value")

        rec = by_pub.get(pub)
        if rec is None:
            rec = {"pub": pub, "title": title, "year": year, "doi": None}
            by_pub[pub] = rec

        if not rec.get("title") and title:
            rec["title"] = title
        if not rec.get("year") and year:
            rec["year"] = year

        if rec["doi"] is None and _is_doi_iri(same_as):
            rec["doi"] = same_as

    out = list(by_pub.values())

    out.sort(key=lambda r: (r["doi"] is None, r["pub"]))

    if limit and limit > 0:
        out = out[:limit]
    return out

def fetch_pub_authors(endpoint_query: str, pub_uri: str) -> List[Dict]:
    q = f"""
    select ?person ?name ?sameAs where {{
        <{pub_uri}> <{SCHEMA}author> ?person .
        optional {{ ?person <{SCHEMA}name> ?name . }}
        optional {{ ?person <{SCHEMA}sameAs> ?sameAs . }}
    }}
    """.strip()

    rows = sparql_select(endpoint_query, q)
    out = []
    for b in rows:
        same_as = b.get("sameAs", {}).get("value")
        out.append({
            "person": b["person"]["value"],
            "name": b.get("name", {}).get("value"),
            "orcid": same_as if same_as and "orcid.org/" in same_as.lower() else None,
        })
    return out
