#!/usr/bin/env python3

import argparse
import json
import re
from pathlib import Path

import bibtexparser
from bibtexparser.bparser import BibTexParser
from bibtexparser.customization import convert_to_unicode
from rdflib import URIRef, Literal
from SPARQLWrapper import SPARQLWrapper, JSON


SCHEMA_KEYWORDS = URIRef("https://schema.org/keywords")
KEYWORD_VALUE = "trr_318"


def normalize_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]", "", title.lower())


def sparql_string(value: str) -> str:
    return json.dumps(value)


def load_bib_entries(path: Path):
    parser = BibTexParser(common_strings=True)
    parser.ignore_nonstandard_types = False
    parser.homogenize_fields = False
    parser.customization = convert_to_unicode

    with path.open("r", encoding="utf-8") as f:
        db = bibtexparser.load(f, parser=parser)

    return db.entries


def query_by_tokenized_title(endpoint: str, tokenized_title: str):
    sparql = SPARQLWrapper(endpoint)
    sparql.setReturnFormat(JSON)

    query = f"""
SELECT ?s ?title
WHERE {{
  ?s <https://schema.org/name> ?title .

  BIND({sparql_string(tokenized_title)} AS ?input)

  BIND(REPLACE(LCASE(STR(?input)), "[^a-z0-9]", "") AS ?inputNorm)
  BIND(REPLACE(LCASE(STR(?title)), "[^a-z0-9]", "") AS ?titleNorm)

  FILTER(?titleNorm = ?inputNorm)
}}
LIMIT 1
"""

    sparql.setQuery(query)
    result = sparql.query().convert()

    bindings = result.get("results", {}).get("bindings", [])
    if not bindings:
        return None

    row = bindings[0]

    return {
        "subject": row["s"]["value"],
        "title": row["title"]["value"],
    }


def nt_literal_triple(subject: str, predicate: URIRef, obj: str) -> str:
    return f"{URIRef(subject).n3()} {predicate.n3()} {Literal(obj).n3()} ."


def main():
    argp = argparse.ArgumentParser()
    argp.add_argument("--bib", required=True, help="Input .bib file")
    argp.add_argument("--out-nt", required=True, help="Output .nt file")
    argp.add_argument(
        "--endpoint",
        default="http://131.234.26.202:9080/sparql",
        help="SPARQL endpoint",
    )
    args = argp.parse_args()

    bib_path = Path(args.bib)
    out_path = Path(args.out_nt)

    entries = load_bib_entries(bib_path)

    written = 0
    skipped_no_title = 0
    skipped_no_match = 0
    seen_triples = set()

    with out_path.open("w", encoding="utf-8") as out:
        for entry in entries:
            raw_title = entry.get("title")

            if not raw_title:
                skipped_no_title += 1
                continue

            tokenized_title = normalize_title(raw_title)

            try:
                match = query_by_tokenized_title(args.endpoint, tokenized_title)
            except Exception as e:
                print(f"[ERROR] Query failed for title: {raw_title}")
                print(f"        {e}")
                continue

            if not match:
                skipped_no_match += 1
                print(f"[MISS] {raw_title}")
                continue

            triple = nt_literal_triple(
                match["subject"],
                SCHEMA_KEYWORDS,
                KEYWORD_VALUE,
            )

            if triple not in seen_triples:
                out.write(triple + "\n")
                seen_triples.add(triple)
                written += 1

            print(f"[OK] {raw_title}")
            print(f"     matched: {match['title']}")
            print(f"     wrote: {triple}")

    print()
    print(f"Entries total: {len(entries)}")
    print(f"Written triples: {written}")
    print(f"Skipped no title: {skipped_no_title}")
    print(f"Skipped no SPARQL match: {skipped_no_match}")
    print(f"Wrote: {out_path}")


if __name__ == "__main__":
    main()