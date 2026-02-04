from typing import Dict, List, Optional
import requests

SCHEMA = "https://schema.org/"
PAPER = "https://dice-research.org/id/publication/ris/"

def sparql_select(endpoint_query: str, sparql: str) -> List[Dict]:
    r = requests.post(
        endpoint_query,
        data={"query": sparql},
        headers={"Accept": "application/sparql-results+json"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json().get("results", {}).get("bindings", [])

def fetch_publications(endpoint_query: str, limit: int = 0, offset:int = 0) -> List[Dict]:
    limit = int(limit)
    offset = int(offset)

    limit_clause = f"limit {limit}" if limit > 0 else ""
    offset_clause = f"offset {offset}" if offset > 0 else ""

    q = f"""
    prefix schema: <{SCHEMA}>
    prefix paper: <{PAPER}>

    select 
        ?pub
        (sample(?title0) as ?title)
        (sample(?year0) as ?year)
        (sample(?doiSameAs0) as ?doi_sameas)
        (sample(?doiIdent0) as ?doi_ident)
    where {{
        ?pub schema:name ?title0 .

        filter(strstarts(str(?pub), str(paper:)))

        optional {{ ?pub schema:datePublished ?year0 . }}

        optional {{
            ?pub schema:sameAs ?doiSameAs0 .
            filter(contains(lcase(str(?doiSameAs0)), "doi.org/"))
      }}

      optional {{
        ?pub schema:identifier ?doiIdent0 .
        filter(isLiteral(?doiIdent0))
        filter(regex(str(?doiIdent0), "^[ ]*doi[ ]*:", "i"))
      }}
    }}
    group by ?pub
    order by ?pub
    {limit_clause}
    {offset_clause}
    """.strip()

    rows = sparql_select(endpoint_query, q)
    out = []

    for b in rows:
        out.append({
            "pub": b["pub"]["value"],
            "title": b.get("title", {}).get("value"),
            "year": b.get("year", {}).get("value"),
            "doi_sameas": b.get("doi_sameas", {}).get("value"),
            "doi_ident": b.get("doi_ident", {}).get("value"),
        })
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
