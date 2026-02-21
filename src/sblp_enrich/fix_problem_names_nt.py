#!/usr/bin/env python3
"""Repair common schema.org/name corruption patterns in N-Triples.

Pipeline per literal (streaming):
1) Convert umlaut-looking patterns: \\"a|o|u followed by whitespace.
2) Unescape N-Triples escapes.
3) Merge split tokens around standalone umlauts.
4) Merge single-letter prefix with umlaut-starting continuation.
5) Remove spaces after hyphen in names.
6) NFC normalize.
7) Optionally log suspicious leftovers.
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from dataclasses import dataclass
from typing import TextIO

DEFAULT_PREDICATE = "<https://schema.org/name>"

UMLAUT_MAP = {
    "a": "ä",
    "o": "ö",
    "u": "ü",
    "A": "Ä",
    "O": "Ö",
    "U": "Ü",
}

UMLAUT_SPACE_RE = re.compile(r'\\"([aAoOuU])(?=\s)')
MERGE_SPLIT_UMLAUT_RE = re.compile(r"([^\W\d_]+)\s+([äöüÄÖÜ])\s+([^\W\d_]+)", re.UNICODE)
MERGE_SINGLE_PREFIX_RE = re.compile(r"\b([^\W\d_])\s+([äöüÄÖÜ][^\W\d_]+)\b", re.UNICODE)
FIX_HYPHEN_SPACE_RE = re.compile(r"\b([A-ZÄÖÜ][^\s-]*)-\s+([A-ZÄÖÜ][^\s-]*)\b", re.UNICODE)
SUSPICIOUS_RAW_UMLAUT_RE = re.compile(r'\\"[aAoOuU](?=\s)')
SUSPICIOUS_ISOLATED_UMLAUT_RE = re.compile(r"(?:^|\s)[äöüÄÖÜ](?:\s|$)", re.UNICODE)
MERGE_STOPWORDS = {
    "and",
    "for",
    "in",
    "of",
    "on",
    "the",
    "to",
    "und",
    "von",
}


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


def alpha_fragment_left(text: str, idx: int) -> str:
    i = idx - 1
    while i >= 0 and text[i].isspace():
        i -= 1
    j = i
    while j >= 0 and text[j].isalpha():
        j -= 1
    return text[j + 1 : i + 1]


def alpha_fragment_right(text: str, idx: int) -> str:
    i = idx
    n = len(text)
    while i < n and text[i].isspace():
        i += 1
    j = i
    while j < n and text[j].isalpha():
        j += 1
    return text[i:j]


def convert_umlaut_looking(raw_literal_body: str) -> str:
    def repl(match: re.Match[str]) -> str:
        left = alpha_fragment_left(raw_literal_body, match.start())
        left_len = len(left)
        right = alpha_fragment_right(raw_literal_body, match.end(1))
        right_len = len(right)

        # Conservative rule: only convert when this looks like a split word fragment.
        if left_len == 0 or right_len == 0:
            return match.group(0)

        # Strong evidence: final single-letter suffix, except common stopwords ("for \"u r").
        if right_len == 1:
            if left.lower() in MERGE_STOPWORDS:
                return match.group(0)
            return UMLAUT_MAP[match.group(1)]

        # Common split pattern: short prefix (e.g., "J \"o rg", "St \"o cklein").
        if left_len <= 2:
            return UMLAUT_MAP[match.group(1)]

        return match.group(0)

    return UMLAUT_SPACE_RE.sub(repl, raw_literal_body)


def merge_split_umlaut_tokens(text: str, max_passes: int = 6) -> str:
    def should_merge(left: str, right: str) -> bool:
        if not left or not right:
            return False
        if left.lower() in MERGE_STOPWORDS and len(right) == 1:
            return False
        if len(left) > 3 and len(right) > 3:
            return False
        return True

    s = text
    for _ in range(max_passes):
        changed = False

        def repl(match: re.Match[str]) -> str:
            nonlocal changed
            left = match.group(1)
            uml = match.group(2)
            right = match.group(3)
            if should_merge(left, right):
                changed = True
                return left + uml + right
            return match.group(0)

        ns = MERGE_SPLIT_UMLAUT_RE.sub(repl, s)
        s = ns
        if not changed:
            break
    return s


def fix_bad_name_literal(raw_literal_body: str) -> str:
    # Pass 1: repair escaped umlaut-looking patterns with conservative split-word checks.
    s = convert_umlaut_looking(raw_literal_body)

    # Pass 2: standard N-Triples unescape.
    s = unescape_nt_literal(s)

    # Pass 3: merge split tokens around standalone umlauts.
    s = merge_split_umlaut_tokens(s)

    # Pass 4: merge single-letter prefix + umlaut-start token.
    s = repeat_sub(MERGE_SINGLE_PREFIX_RE, r"\1\2", s)

    # Pass 5: remove spaces after hyphen in names.
    s = repeat_sub(FIX_HYPHEN_SPACE_RE, r"\1-\2", s)

    # Pass 6: NFC normalization.
    s = unicodedata.normalize("NFC", s)
    return s


def detect_suspicious(raw_escaped_after: str, repaired_unescaped: str, check_odd_quotes: bool) -> list[str]:
    reasons: list[str] = []
    if SUSPICIOUS_RAW_UMLAUT_RE.search(raw_escaped_after):
        reasons.append("raw_escaped_umlaut_pattern")
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
