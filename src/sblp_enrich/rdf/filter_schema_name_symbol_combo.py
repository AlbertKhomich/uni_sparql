#!/usr/bin/env python3
"""Filter N-Triples for schema.org/name literals containing a symbol combo.

Default behavior prints triples where:
- predicate is <https://schema.org/name>
- object is a literal containing the raw substring '\\"'

Examples:
  python3 filter_schema_name_symbol_combo.py data.nt
  python3 filter_schema_name_symbol_combo.py data.nt --contains '\\\\"' > matches.nt
  cat data.nt | python3 filter_schema_name_symbol_combo.py -
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from typing import TextIO

DEFAULT_PREDICATE = "<https://schema.org/name>"


@dataclass
class ParsedLine:
    predicate: str
    obj_kind: str
    lit_lex: str | None


def parse_nt_line(line: str) -> ParsedLine | None:
    s = line.rstrip("\n")
    n = len(s)
    i = 0

    def skip_ws(idx: int) -> int:
        while idx < n and s[idx].isspace():
            idx += 1
        return idx

    def parse_iri(idx: int) -> tuple[str, int] | None:
        if idx >= n or s[idx] != "<":
            return None
        j = s.find(">", idx + 1)
        if j == -1:
            return None
        return s[idx : j + 1], j + 1

    def parse_bnode(idx: int) -> tuple[str, int] | None:
        if not s.startswith("_:", idx):
            return None
        j = idx + 2
        while j < n and not s[j].isspace():
            j += 1
        if j == idx + 2:
            return None
        return s[idx:j], j

    def parse_literal(idx: int) -> tuple[str, int] | None:
        if idx >= n or s[idx] != '"':
            return None
        j = idx + 1
        while j < n:
            c = s[j]
            if c == "\\":
                j += 2
                continue
            if c == '"':
                break
            j += 1
        if j >= n or s[j] != '"':
            return None

        lex = s[idx + 1 : j]
        k = j + 1
        if k < n and s[k] == "@":
            m = k + 1
            while m < n and (s[m].isalnum() or s[m] == "-"):
                m += 1
            k = m
        elif k + 1 < n and s[k : k + 2] == "^^":
            iri = parse_iri(k + 2)
            if iri is None:
                return None
            _, k = iri
        return lex, k

    def parse_object(idx: int) -> tuple[str, str | None, int] | None:
        iri = parse_iri(idx)
        if iri is not None:
            _, end = iri
            return "iri", None, end
        bnode = parse_bnode(idx)
        if bnode is not None:
            _, end = bnode
            return "bnode", None, end
        lit = parse_literal(idx)
        if lit is not None:
            lex, end = lit
            return "literal", lex, end
        return None

    i = skip_ws(i)
    subj = parse_iri(i) or parse_bnode(i)
    if subj is None:
        return None
    _, i = subj

    i = skip_ws(i)
    pred = parse_iri(i)
    if pred is None:
        return None
    predicate, i = pred

    i = skip_ws(i)
    obj = parse_object(i)
    if obj is None:
        return None
    obj_kind, lit_lex, i = obj

    i = skip_ws(i)
    if i >= n or s[i] != ".":
        return None
    i += 1
    i = skip_ws(i)
    if i != n:
        return None

    return ParsedLine(predicate=predicate, obj_kind=obj_kind, lit_lex=lit_lex)


def iter_lines(path: str) -> tuple[TextIO, bool]:
    if path == "-":
        return sys.stdin, False
    return open(path, "r", encoding="utf-8", errors="replace"), True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Print N-Triples where predicate is schema.org/name and object literal contains a symbol combination."
        )
    )
    parser.add_argument(
        "inputs",
        nargs="+",
        help="Input .nt files. Use '-' to read from stdin.",
    )
    parser.add_argument(
        "--predicate",
        default=DEFAULT_PREDICATE,
        help=f"Predicate IRI to match (default: {DEFAULT_PREDICATE}).",
    )
    parser.add_argument(
        "--contains",
        default='\\"',
        help="Raw substring to search inside literal lexical form. Default is backslash+quote.",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Print counts to stderr.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    total = 0
    parsed = 0
    matched = 0

    for path in args.inputs:
        handle, should_close = iter_lines(path)
        try:
            for line in handle:
                total += 1
                parsed_line = parse_nt_line(line)
                if parsed_line is None:
                    continue
                parsed += 1
                if parsed_line.predicate != args.predicate:
                    continue
                if parsed_line.obj_kind != "literal" or parsed_line.lit_lex is None:
                    continue
                if args.contains in parsed_line.lit_lex:
                    matched += 1
                    sys.stdout.write(line)
        finally:
            if should_close:
                handle.close()

    if args.stats:
        sys.stderr.write(
            f"lines={total} parsed={parsed} matched={matched} contains={args.contains!r} predicate={args.predicate}\n"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
