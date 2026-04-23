from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import Iterable, TextIO

from sblp_enrich.providers.fuseki_schemaorg import sparql_select

DEFAULT_ENDPOINT = "http://upbkg.data.dice-research.org/sparql"


def _clean_name(name: str) -> str:
    return " ".join(name.split()).strip()


def _read_names(stream: Iterable[str]) -> list[str]:
    names: list[str] = []
    for raw_line in stream:
        cleaned = _clean_name(raw_line)
        if not cleaned or cleaned.startswith("#"):
            continue
        names.append(cleaned)
    return names


def _load_input_names(positional_names: list[str], input_file: str | None) -> list[str]:
    names: list[str] = []
    for name in positional_names:
        cleaned = _clean_name(name)
        if cleaned:
            names.append(cleaned)

    if input_file:
        path = Path(input_file)
        with path.open("r", encoding="utf-8") as handle:
            names.extend(_read_names(handle))

    if not names and not sys.stdin.isatty():
        names.extend(_read_names(sys.stdin))

    if not names:
        raise ValueError("Provide at least one name via arguments, --input-file, or stdin.")

    return names


def _to_schema_author_name(name: str) -> str:
    cleaned = _clean_name(name)
    if not cleaned:
        raise ValueError("Author name is empty.")

    if "," in cleaned:
        return cleaned

    parts = cleaned.split(" ")
    if len(parts) == 1:
        return cleaned

    # Move the last token to the front to match literals like "Family, Given".
    return f"{parts[-1]}, {' '.join(parts[:-1])}"


def _sparql_string_literal(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def _normalize_query_names(input_names: list[str], name_as_is: bool) -> list[str]:
    query_names: list[str] = []
    seen: set[str] = set()

    for input_name in input_names:
        query_name = input_name if name_as_is else _to_schema_author_name(input_name)
        if query_name in seen:
            continue
        seen.add(query_name)
        query_names.append(query_name)

    return query_names


def _build_author_list(author_names: list[str]) -> str:
    if not author_names:
        raise ValueError("Provide at least one author name.")
    return ", ".join(_sparql_string_literal(name) for name in author_names)


def _having_operator(exact_shared_authors: bool) -> str:
    return "=" if exact_shared_authors else ">="


def _build_filtered_publications_subquery(
    author_names: list[str],
    min_shared_authors: int,
    exact_shared_authors: bool,
) -> str:
    if min_shared_authors < 2:
        raise ValueError("--min-shared-authors must be at least 2.")

    author_list = _build_author_list(author_names)
    having_operator = _having_operator(exact_shared_authors)

    return f"""
  {{
    SELECT ?publication
    WHERE {{
      ?publication schema:author ?matchedAuthor .
      ?matchedAuthor schema:name ?matchedName .

      FILTER(STR(?matchedName) IN ({author_list}))
    }}
    GROUP BY ?publication
    HAVING (COUNT(DISTINCT ?matchedName) {having_operator} {min_shared_authors})
  }}
""".strip()


def build_publication_count_query(
    author_names: list[str],
    min_shared_authors: int,
    exact_shared_authors: bool,
) -> str:
    return f"""
PREFIX schema: <https://schema.org/>

SELECT (COUNT(DISTINCT ?publication) AS ?publicationCount)
WHERE {{
  {_build_filtered_publications_subquery(author_names, min_shared_authors, exact_shared_authors)}
}}
""".strip()


def build_pair_collaboration_query(
    author_names: list[str],
    min_shared_authors: int,
    exact_shared_authors: bool,
) -> str:
    author_list = _build_author_list(author_names)

    return f"""
PREFIX schema: <https://schema.org/>

SELECT
  ?name1
  ?name2
  (COUNT(DISTINCT ?publication) AS ?publicationCount)
WHERE {{
  {_build_filtered_publications_subquery(author_names, min_shared_authors, exact_shared_authors)}

  ?publication schema:author ?author1 .
  ?publication schema:author ?author2 .
  ?author1 schema:name ?name1 .
  ?author2 schema:name ?name2 .

  FILTER(STR(?name1) IN ({author_list}))
  FILTER(STR(?name2) IN ({author_list}))
  FILTER(STR(?name1) < STR(?name2))
}}
GROUP BY ?name1 ?name2
ORDER BY DESC(?publicationCount) ?name1 ?name2
""".strip()


def fetch_publication_count(
    endpoint: str,
    author_names: list[str],
    min_shared_authors: int,
    exact_shared_authors: bool,
) -> int:
    if len(author_names) < min_shared_authors:
        return 0

    rows = sparql_select(
        endpoint,
        build_publication_count_query(
            author_names,
            min_shared_authors,
            exact_shared_authors,
        ),
    )
    if not rows:
        return 0

    raw_count = rows[0].get("publicationCount", {}).get("value", "0")
    return int(raw_count)


def fetch_pair_collaborations(
    endpoint: str,
    author_names: list[str],
    min_shared_authors: int,
    exact_shared_authors: bool,
) -> list[dict[str, str | int]]:
    if len(author_names) < 2:
        return []

    rows = sparql_select(
        endpoint,
        build_pair_collaboration_query(
            author_names,
            min_shared_authors,
            exact_shared_authors,
        ),
    )

    pairs: list[dict[str, str | int]] = []
    for row in rows:
        name1 = row.get("name1", {}).get("value", "")
        name2 = row.get("name2", {}).get("value", "")
        raw_count = row.get("publicationCount", {}).get("value", "0")
        pairs.append(
            {
                "author_1": name1,
                "author_2": name2,
                "collaboration_publication_count": int(raw_count),
            }
        )

    return pairs


def _write_summary_results(
    output: TextIO,
    input_names: list[str],
    query_names: list[str],
    endpoint: str,
    min_shared_authors: int,
    exact_shared_authors: bool,
    no_header: bool,
) -> None:
    writer = csv.writer(output, delimiter="\t", lineterminator="\n")

    if not no_header:
        writer.writerow(
            [
                "input_author_count",
                "query_author_count",
                "shared_author_mode",
                "min_shared_authors",
                "collaboration_publication_count",
            ]
        )

    count = fetch_publication_count(
        endpoint,
        query_names,
        min_shared_authors,
        exact_shared_authors,
    )
    mode = "exact" if exact_shared_authors else "at_least"
    writer.writerow([len(input_names), len(query_names), mode, min_shared_authors, count])


def _write_pair_results(
    output: TextIO,
    query_names: list[str],
    endpoint: str,
    min_shared_authors: int,
    exact_shared_authors: bool,
    no_header: bool,
) -> None:
    writer = csv.writer(output, delimiter="\t", lineterminator="\n")

    if not no_header:
        writer.writerow(
            [
                "author_1",
                "author_2",
                "collaboration_publication_count",
            ]
        )

    for pair in fetch_pair_collaborations(
        endpoint=endpoint,
        author_names=query_names,
        min_shared_authors=min_shared_authors,
        exact_shared_authors=exact_shared_authors,
    ):
        writer.writerow(
            [
                pair["author_1"],
                pair["author_2"],
                pair["collaboration_publication_count"],
            ]
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Count publications on which at least N provided authors appear together "
            "via exact schema:name matches on the UPB knowledge graph SPARQL endpoint."
        )
    )
    parser.add_argument(
        "names",
        nargs="*",
        help=(
            "Author names in 'Given Family' form. Names that already contain a comma "
            "are used as-is."
        ),
    )
    parser.add_argument(
        "--input-file",
        help="Read one author name per line from a UTF-8 text file.",
    )
    parser.add_argument(
        "--endpoint",
        default=DEFAULT_ENDPOINT,
        help=f"SPARQL endpoint to query. Default: {DEFAULT_ENDPOINT}",
    )
    parser.add_argument(
        "--name-as-is",
        action="store_true",
        help="Use each provided name exactly as the schema:name literal.",
    )
    parser.add_argument(
        "--min-shared-authors",
        type=int,
        default=2,
        help="Count publications with at least this many authors from the provided list.",
    )
    parser.add_argument(
        "--exact-shared-authors",
        action="store_true",
        help="Require exactly --min-shared-authors matched names on a publication.",
    )
    parser.add_argument(
        "--pairs",
        action="store_true",
        help="List collaborating author pairs and their shared-publication counts.",
    )
    parser.add_argument(
        "--no-header",
        action="store_true",
        help="Do not print the TSV header row.",
    )
    args = parser.parse_args()

    try:
        input_names = _load_input_names(args.names, args.input_file)
        query_names = _normalize_query_names(input_names, args.name_as_is)
        if args.pairs:
            _write_pair_results(
                output=sys.stdout,
                query_names=query_names,
                endpoint=args.endpoint,
                min_shared_authors=args.min_shared_authors,
                exact_shared_authors=args.exact_shared_authors,
                no_header=args.no_header,
            )
        else:
            _write_summary_results(
                output=sys.stdout,
                input_names=input_names,
                query_names=query_names,
                endpoint=args.endpoint,
                min_shared_authors=args.min_shared_authors,
                exact_shared_authors=args.exact_shared_authors,
                no_header=args.no_header,
            )
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
