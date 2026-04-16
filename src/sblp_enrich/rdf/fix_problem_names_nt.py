#!/usr/bin/env python3
r"""Repair common schema.org/name corruption patterns in N-Triples.

Pipeline per literal (streaming):
1) Unescape N-Triples escapes.
2) Repair LaTeX-style accent fragments (\\"u, \\ "u, \\"{u}, \ss).
3) Merge split tokens around standalone umlauts.
4) Merge single-letter prefix with umlaut-starting continuation.
5) Remove spaces after hyphen in split names.
6) NFC normalize.
7) Re-escape as valid N-Triples.
8) Optionally log suspicious leftovers.
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from dataclasses import dataclass
from typing import TextIO

DEFAULT_PREDICATE = "<https://schema.org/name>"

MERGE_SPLIT_UMLAUT_RE = re.compile(r"([^\W\d_]+)\s+([äöüÄÖÜ])\s+([^\W\d_]+)", re.UNICODE)
MERGE_SINGLE_PREFIX_RE = re.compile(r"\b([^\W\d_])\s+([äöüÄÖÜ][^\W\d_]+)\b", re.UNICODE)
MERGE_TITLECASE2_PREFIX_RE = re.compile(r"\b([A-ZÄÖÜ][a-zäöüß])\s+([äöü][^\W\d_]+)\b", re.UNICODE)
FIX_HYPHEN_SPACE_RE = re.compile(r"(\w)-\s+(\w)", re.UNICODE)
# Braced LaTeX umlaut forms: \"{u}
DIAERESIS_BRACED_RE = re.compile(r'\\\s*"\s*\{\s*([A-Za-z])\s*\}')
# Split-token umlaut forms that keep trailing split whitespace: \" u r / \"u r / \ "u r
DIAERESIS_SPLIT_RE = re.compile(r'\\\s*"\s*([A-Za-z])(?=\s)')
# Sharp-s in-word split form: ... \ ss ...
SHARP_S_BETWEEN_RE = re.compile(r"([^\W\d_])\s*\\\s*(ss|SS)\s*([^\W\d_])", re.UNICODE)
# Fallback standalone sharp-s macro: \ss / \SS
SHARP_S_RE = re.compile(r"\\\s*(ss|SS)\b")
SUSPICIOUS_RAW_UMLAUT_RE = re.compile(r'\\\\\s*\\"\s*\{?\s*[A-Za-z]\s*\}?')
SUSPICIOUS_RAW_SHARP_S_RE = re.compile(r"\\\\\s*(ss|SS)\b")
SUSPICIOUS_ISOLATED_UMLAUT_RE = re.compile(r"(?:^|\s)[äöüÄÖÜ](?:\s|$)", re.UNICODE)
FOR_UR_RE = re.compile(r"\bforür\b")
SPACE_BEFORE_COMMA_RE = re.compile(r"\s+,")


@dataclass
class ParsedLine:
    subject: str
    predicate: str
    object_text: str
    obj_kind: str
    lit_lex: str | None
    lit_suffix: str | None


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

    def parse_literal(idx: int) -> tuple[str, str, int] | None:
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
        suffix = ""
        if k < n and s[k] == "@":
            m = k + 1
            while m < n and (s[m].isalnum() or s[m] == "-"):
                m += 1
            suffix = s[k:m]
            k = m
        elif k + 1 < n and s[k : k + 2] == "^^":
            iri = parse_iri(k + 2)
            if iri is None:
                return None
            iri_text, iri_end = iri
            suffix = "^^" + iri_text
            k = iri_end

        return lex, suffix, k

    def parse_object(idx: int) -> tuple[str, str | None, str | None, int, str] | None:
        iri = parse_iri(idx)
        if iri is not None:
            text, end = iri
            return text, None, None, end, "iri"

        bnode = parse_bnode(idx)
        if bnode is not None:
            text, end = bnode
            return text, None, None, end, "bnode"

        lit = parse_literal(idx)
        if lit is not None:
            lex, suffix, end = lit
            text = f'"{lex}"{suffix}'
            return text, lex, suffix, end, "literal"

        return None

    i = skip_ws(i)
    subj = parse_iri(i) or parse_bnode(i)
    if subj is None:
        return None
    subject, i = subj

    i = skip_ws(i)
    pred = parse_iri(i)
    if pred is None:
        return None
    predicate, i = pred

    i = skip_ws(i)
    obj = parse_object(i)
    if obj is None:
        return None
    object_text, lit_lex, lit_suffix, i, obj_kind = obj

    i = skip_ws(i)
    if i >= n or s[i] != ".":
        return None
    i += 1
    i = skip_ws(i)
    if i != n:
        return None

    return ParsedLine(
        subject=subject,
        predicate=predicate,
        object_text=object_text,
        obj_kind=obj_kind,
        lit_lex=lit_lex,
        lit_suffix=lit_suffix,
    )


def unescape_nt_literal(text: str) -> str:
    out: list[str] = []
    i = 0
    n = len(text)

    simple = {
        "t": "\t",
        "b": "\b",
        "n": "\n",
        "r": "\r",
        "f": "\f",
        '"': '"',
        "'": "'",
        "\\": "\\",
    }

    while i < n:
        c = text[i]
        if c != "\\" or i + 1 >= n:
            out.append(c)
            i += 1
            continue

        nxt = text[i + 1]
        if nxt in simple:
            out.append(simple[nxt])
            i += 2
            continue

        if nxt == "u" and i + 5 < n:
            h = text[i + 2 : i + 6]
            if all(ch in "0123456789abcdefABCDEF" for ch in h):
                out.append(chr(int(h, 16)))
                i += 6
                continue

        if nxt == "U" and i + 9 < n:
            h = text[i + 2 : i + 10]
            if all(ch in "0123456789abcdefABCDEF" for ch in h):
                out.append(chr(int(h, 16)))
                i += 10
                continue

        out.append("\\")
        out.append(nxt)
        i += 2

    return "".join(out)


def escape_nt_literal(text: str) -> str:
    out: list[str] = []
    for ch in text:
        o = ord(ch)
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\b":
            out.append("\\b")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\f":
            out.append("\\f")
        elif o < 0x20 or o == 0x7F:
            out.append(f"\\u{o:04X}")
        else:
            out.append(ch)
    return "".join(out)


def repeat_sub(pattern: re.Pattern[str], repl: str, text: str, max_passes: int = 6) -> str:
    s = text
    for _ in range(max_passes):
        ns = pattern.sub(repl, s)
        if ns == s:
            break
        s = ns
    return s


def to_diaeresis(letter: str) -> str:
    """Convert ASCII letter to diaeresis counterpart via Unicode composition."""
    return unicodedata.normalize("NFC", letter + "\u0308")


def merge_split_umlaut_tokens(text: str, max_passes: int = 6) -> str:
    return repeat_sub(MERGE_SPLIT_UMLAUT_RE, r"\1\2\3", text, max_passes=max_passes)


def fix_bad_name_literal(raw_literal_body: str) -> str:
    # Pass 1: standard N-Triples unescape.
    s = unescape_nt_literal(raw_literal_body)

    # Pass 2: repair LaTeX diaeresis fragments (\\"u, \\ "u, \\"{u}).
    s = DIAERESIS_BRACED_RE.sub(lambda m: to_diaeresis(m.group(1)), s)
    s = DIAERESIS_SPLIT_RE.sub(lambda m: to_diaeresis(m.group(1)), s)

    # Pass 3: repair LaTeX sharp-s fragments (\ss, \SS).
    s = SHARP_S_BETWEEN_RE.sub(lambda m: m.group(1) + ("ß" if m.group(2) == "ss" else "ẞ") + m.group(3), s)
    s = SHARP_S_RE.sub(lambda m: "ß" if m.group(1) == "ss" else "ẞ", s)

    # Pass 4: merge split tokens around standalone umlauts.
    s = merge_split_umlaut_tokens(s)

    # Pass 5: merge single-letter prefix + umlaut-start token.
    s = repeat_sub(MERGE_SINGLE_PREFIX_RE, r"\1\2", s)
    s = repeat_sub(MERGE_TITLECASE2_PREFIX_RE, r"\1\2", s)

    # Pass 6: remove spaces after hyphen in names.
    s = repeat_sub(FIX_HYPHEN_SPACE_RE, r"\1-\2", s)

    # Pass 7: normalize known split artifact and punctuation spacing.
    s = FOR_UR_RE.sub("für", s)
    s = SPACE_BEFORE_COMMA_RE.sub(",", s)

    # Pass 8: NFC normalization.
    s = unicodedata.normalize("NFC", s)
    return s


def detect_suspicious(raw_escaped_after: str, repaired_unescaped: str, check_odd_quotes: bool) -> list[str]:
    reasons: list[str] = []
    if SUSPICIOUS_RAW_UMLAUT_RE.search(raw_escaped_after):
        reasons.append("raw_escaped_umlaut_pattern")
    if SUSPICIOUS_RAW_SHARP_S_RE.search(raw_escaped_after):
        reasons.append("raw_escaped_sharp_s_pattern")
    if SUSPICIOUS_ISOLATED_UMLAUT_RE.search(repaired_unescaped):
        reasons.append("isolated_umlaut_token")
    if check_odd_quotes and repaired_unescaped.count('"') % 2 == 1:
        reasons.append("odd_quote_count")
    return reasons


def open_input(path: str) -> tuple[TextIO, bool]:
    if path == "-":
        return sys.stdin, False
    return open(path, "r", encoding="utf-8", errors="replace"), True


def open_output(path: str) -> tuple[TextIO, bool]:
    if path == "-":
        return sys.stdout, False
    return open(path, "w", encoding="utf-8"), True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Repair problem schema.org/name literals in N-Triples.")
    parser.add_argument("--in", dest="input_path", required=True, help="Input .nt file (or '-' for stdin).")
    parser.add_argument("--out", dest="output_path", required=True, help="Output .nt file (or '-' for stdout).")
    parser.add_argument(
        "--predicate",
        default=DEFAULT_PREDICATE,
        help=f"Only fix this predicate (default: {DEFAULT_PREDICATE}).",
    )
    parser.add_argument(
        "--all-literals",
        action="store_true",
        help="Apply repairs to all literal objects, regardless of predicate.",
    )
    parser.add_argument(
        "--suspicious-log",
        help="Optional TSV file for suspicious outputs: line_no, subject, reasons, before, after.",
    )
    parser.add_argument(
        "--check-odd-quotes",
        action="store_true",
        help="Also flag odd counts of straight quotes in repaired literals.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    total_lines = 0
    parsed_lines = 0
    target_literals = 0
    modified_literals = 0
    suspicious_count = 0

    suspicious_fh: TextIO | None = None
    if args.suspicious_log:
        suspicious_fh = open(args.suspicious_log, "w", encoding="utf-8")

    fin, close_in = open_input(args.input_path)
    fout, close_out = open_output(args.output_path)

    try:
        for line_no, line in enumerate(fin, start=1):
            total_lines += 1
            parsed = parse_nt_line(line)
            if parsed is None:
                fout.write(line)
                continue

            parsed_lines += 1
            if parsed.obj_kind != "literal" or parsed.lit_lex is None:
                fout.write(line)
                continue

            if not args.all_literals and parsed.predicate != args.predicate:
                fout.write(line)
                continue

            target_literals += 1
            before_unescaped = unescape_nt_literal(parsed.lit_lex)
            repaired = fix_bad_name_literal(parsed.lit_lex)
            escaped_after = escape_nt_literal(repaired)

            suffix = parsed.lit_suffix or ""
            new_line = f'{parsed.subject} {parsed.predicate} "{escaped_after}"{suffix} .\n'
            fout.write(new_line)

            if repaired != before_unescaped:
                modified_literals += 1

            reasons = detect_suspicious(escaped_after, repaired, args.check_odd_quotes)
            if reasons:
                suspicious_count += 1
                if suspicious_fh is not None:
                    before_compact = before_unescaped.replace("\n", "\\n")
                    after_compact = repaired.replace("\n", "\\n")
                    suspicious_fh.write(
                        f"{line_no}\t{parsed.subject}\t{','.join(reasons)}\t{before_compact}\t{after_compact}\n"
                    )
    finally:
        if suspicious_fh is not None:
            suspicious_fh.close()
        if close_in:
            fin.close()
        if close_out:
            fout.close()

    sys.stderr.write(
        "total_lines={total_lines} parsed_lines={parsed_lines} target_literals={target_literals} modified_literals={modified_literals} suspicious={suspicious_count}\n".format(
            total_lines=total_lines,
            parsed_lines=parsed_lines,
            target_literals=target_literals,
            modified_literals=modified_literals,
            suspicious_count=suspicious_count,
        )
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
