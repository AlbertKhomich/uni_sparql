#!/usr/bin/env python3

import argparse
import json
import re
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

import bibtexparser
import requests
from rdflib import URIRef


SCHEMA_URL = URIRef("https://schema.org/url")


def clean_bib_value(value: str | None) -> str | None:
    if not value:
        return None

    value = value.strip()

    # BibTeX-protected capitalization:
    # {{LOLA}} -> LOLA
    while value.startswith("{") and value.endswith("}"):
        value = value[1:-1].strip()

    # Common BibTeX escapes in URLs/text
    value = value.replace(r"\_", "_")
    value = value.replace(r"\&", "&")
    value = value.replace(r"\%", "%")
    value = value.replace(r"\#", "#")
    value = value.replace(r"\$", "$")
    value = value.replace(r"\{", "{")
    value = value.replace(r"\}", "}")

    value = re.sub(r"\s+", " ", value)

    return value


def normalize_url_for_uri(url: str) -> str:
    url = clean_bib_value(url)

    if not url:
        raise ValueError("Empty URL")

    parts = urlsplit(url)

    if not parts.scheme or not parts.netloc:
        raise ValueError(f"Not an absolute URL: {url}")

    path = quote(parts.path, safe="/:@-._~!$&'()*+,;=")
    query = quote(parts.query, safe="/?:@-._~!$&'()*+,;=%")
    fragment = quote(parts.fragment, safe="/?:@-._~!$&'()*+,;=%")

    return urlunsplit((parts.scheme, parts.netloc, path, query, fragment))


def sparql_escape(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def build_query(title: str) -> str:
    return f"""
SELECT ?s ?title ?url
WHERE {{
  VALUES ?inputTitle {{
    {sparql_escape(title)}
  }}

  ?s <https://schema.org/name> ?title .

  BIND(
    LCASE(REPLACE(STR(?inputTitle), "[^A-Za-z0-9]", ""))
    AS ?inputNorm
  )

  BIND(
    LCASE(REPLACE(STR(?title), "[^A-Za-z0-9]", ""))
    AS ?titleNorm
  )

  FILTER(?inputNorm = ?titleNorm)

  OPTIONAL {{
    ?s <https://schema.org/url> ?url .
  }}
}}
"""


def ask_endpoint(endpoint: str, title: str) -> dict:
    response = requests.get(
        endpoint,
        params={
            "query": build_query(title),
            "format": "application/sparql-results+json",
        },
        headers={
            "Accept": "application/sparql-results+json",
        },
        timeout=60,
    )
    response.raise_for_status()
    return response.json()


def has_url_result(data: dict) -> bool:
    for binding in data.get("results", {}).get("bindings", []):
        if "url" in binding:
            return True

    return False


def subjects_from_result(data: dict) -> list[str]:
    subjects = set()

    for binding in data.get("results", {}).get("bindings", []):
        subject = binding.get("s", {}).get("value")
        if subject:
            subjects.add(subject)

    return sorted(subjects)


def nt_uri_triple(subject: str, predicate: URIRef, obj_url: str) -> str:
    obj_url = normalize_url_for_uri(obj_url)
    return f"{URIRef(subject).n3()} {predicate.n3()} {URIRef(obj_url).n3()} ."


def load_bib_entries(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        bib_database = bibtexparser.load(f)

    return bib_database.entries


def get_candidate_urls(entry: dict) -> list[str]:
    raw_urls = [
        entry.get("url"),
        entry.get("bdsk-url-1"),
    ]

    urls = []

    for raw_url in raw_urls:
        if not raw_url:
            continue

        try:
            urls.append(normalize_url_for_uri(raw_url))
        except ValueError as exc:
            print(f"  Skip invalid URL: {raw_url} ({exc})")

    return sorted(set(urls))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("bibfile", help="Input .bib file")
    parser.add_argument(
        "-e",
        "--endpoint",
        default="http://131.234.26.202:9080/sparql",
        help="SPARQL endpoint",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="missing_urls.nt",
        help="Output .nt file",
    )

    args = parser.parse_args()

    bib_path = Path(args.bibfile)
    out_path = Path(args.output)

    entries = load_bib_entries(bib_path)

    triples = []
    seen_triples = set()

    for entry in entries:
        title = clean_bib_value(entry.get("title"))
        if not title:
            continue

        candidate_urls = get_candidate_urls(entry)
        if not candidate_urls:
            continue

        print(f"Checking: {title}")

        try:
            data = ask_endpoint(args.endpoint, title)
        except Exception as exc:
            print(f"  Endpoint error: {exc}")
            continue

        if has_url_result(data):
            print("  Existing schema:url found, skipping")
            continue

        subjects = subjects_from_result(data)

        if not subjects:
            print("  No matching subject found, skipping")
            continue

        for subject in subjects:
            for url in candidate_urls:
                try:
                    triple = nt_uri_triple(subject, SCHEMA_URL, url)
                except Exception as exc:
                    print(f"  Could not create triple for URL {url}: {exc}")
                    continue

                if triple in seen_triples:
                    continue

                triples.append(triple)
                seen_triples.add(triple)

                print(f"  Add: {triple}")

    out_path.write_text(
        "\n".join(triples) + ("\n" if triples else ""),
        encoding="utf-8",
    )

    print(f"\nSaved {len(triples)} triples to {out_path}")


if __name__ == "__main__":
    main()