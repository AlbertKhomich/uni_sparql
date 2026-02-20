#!/usr/bin/env python3
import argparse
import html
import re
import shutil
import unicodedata
from pathlib import Path

try:
    from pylatexenc.latex2text import LatexNodes2Text  # type: ignore
except Exception:
    LatexNodes2Text = None


ACCENT_TO_COMBINING = {
    '"': "\u0308",
    "'": "\u0301",
    "’": "\u0301",
    "´": "\u0301",
    "`": "\u0300",
    "~": "\u0303",
    "c": "\u0327",
    "^": "\u0302",
    "v": "\u030C",
}
ACCENT_RE = re.compile(
    r"\{?\s*(?:\\\s*)+([\"'`~^cv’´])\s*(?:\{\s*(\\i|\\j|[^\W\d_])\s*\}|(\\i|\\j|[^\W\d_]))\s*\}?"
)
SPECIAL_CMD_MAP = {
    "ss": "ß",
    "SS": "ẞ",
    "o": "ø",
    "O": "Ø",
    "ae": "æ",
    "AE": "Æ",
    "oe": "œ",
    "OE": "Œ",
    "aa": "å",
    "AA": "Å",
    "l": "ł",
    "L": "Ł",
    "i": "i",
    "j": "j",
}
SPECIAL_CMD_RE = re.compile(
    r"\{?\s*(?:\\\s*)+(ss|SS|ae|AE|oe|OE|aa|AA|o|O|l|L|i|j)\s*\}?"
)
SPACING_CMD_RE = re.compile(r"\\\s+|\\[,;:!]")
ESCAPED_PUNCT_RE = re.compile(r"\\([&#%_{}])")
SINGLE_BRACE_RE = re.compile(r"\{([^{}\s])\}")
BROKEN_INITIAL_RE = re.compile(r"\b([^\W\d_])\{\s*\\[^}]*\}", re.UNICODE)
BROKEN_NAME_SPLIT_RE = re.compile(r"(?<=[^\W\d_])\{\s*\\[,;:!]\s*", re.UNICODE)
MULTI_SPACE_RE = re.compile(r"\s{2,}")
PAREN_GROUP_RE = re.compile(r"\(\s*([^()]*)\s*\)")
SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([,;:.])")
DOI_ANCHOR_RE = re.compile(r"^\s*<a\b[^>]*>\s*([^<]+?)\s*</a>\s*$", re.IGNORECASE)
DOI_URL_RE = re.compile(
    r"https?://(?:dx\.)?doi\.org/(10\.[^ \t\r\n<>\"}]+)", re.IGNORECASE
)
DOI_PREFIX_RE = re.compile(r"^\s*doi:\s*", re.IGNORECASE)
NEXT_FIELD_RE = re.compile(r"\s*,?\s*[A-Za-z_][\w-]*\s*=")
FIELD_NAMES = {
    "author",
    "booktitle",
    "collection",
    "doi",
    "edition",
    "editor",
    "journal",
    "number",
    "pages",
    "place",
    "publisher",
    "series",
    "title",
    "volume",
    "year",
}
COMMA_NEXT_KNOWN_FIELD_RE = re.compile(
    r",\s*(?:"
    + "|".join(map(re.escape, sorted(FIELD_NAMES, key=len, reverse=True)))
    + r")\s*=",
    re.IGNORECASE,
)

_l2t = LatexNodes2Text() if LatexNodes2Text else None


def _apply_accent(mark_cmd: str, base: str) -> str:
    mark = ACCENT_TO_COMBINING.get(mark_cmd)
    if mark is None:
        return "\\" + mark_cmd + base
    return unicodedata.normalize("NFC", base + mark)


def _convert_latex_accents(text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        base = match.group(2) or match.group(3)
        if not base:
            return match.group(0)
        base = base.strip()
        if base in ("\\i", "i", "ı"):
            base = "i"
        elif base in ("\\j", "j"):
            base = "j"
        elif len(base) != 1 or not base.isalpha():
            return match.group(0)
        return _apply_accent(match.group(1), base)

    s = text
    for _ in range(3):
        ns = ACCENT_RE.sub(repl, s)
        if ns == s:
            break
        s = ns
    return s


def _convert_special_commands(text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        cmd = match.group(1)
        return SPECIAL_CMD_MAP.get(cmd, match.group(0))

    s = text
    for _ in range(3):
        ns = SPECIAL_CMD_RE.sub(repl, s)
        if ns == s:
            break
        s = ns
    return s


def decode_latex_value(text: str) -> str:
    s = html.unescape(html.unescape(text))
    # Repair common malformed-name fragment before any decoder can erase the signal:
    # "Kl{\\, Benjamin" -> "Kl, Benjamin"
    s = BROKEN_NAME_SPLIT_RE.sub(", ", s)
    if _l2t is not None:
        try:
            s = _l2t.latex_to_text(s)
        except Exception:
            pass
    s = _convert_latex_accents(s)
    s = _convert_special_commands(s)
    s = BROKEN_INITIAL_RE.sub(r"\1", s)
    s = SPACING_CMD_RE.sub(" ", s)
    s = ESCAPED_PUNCT_RE.sub(r"\1", s)
    for _ in range(4):
        ns = SINGLE_BRACE_RE.sub(r"\1", s).replace("{}", "")
        if ns == s:
            break
        s = ns
    s = MULTI_SPACE_RE.sub(" ", s).strip()
    return s


def _field_name_before_equals(text: str, eq_index: int) -> str:
    i = eq_index - 1
    while i >= 0 and text[i].isspace():
        i -= 1
    end = i + 1
    while i >= 0 and (text[i].isalnum() or text[i] in "_-"):
        i -= 1
    start = i + 1
    if start < end:
        return text[start:end].lower()
    return ""


def _clean_author_value(text: str) -> str:
    s = text
    for _ in range(4):
        ns = PAREN_GROUP_RE.sub(r"\1", s)
        if ns == s:
            break
        s = ns
    s = SPACE_BEFORE_PUNCT_RE.sub(r"\1", s)
    s = MULTI_SPACE_RE.sub(" ", s).strip()
    return s


def _clean_doi_value(text: str) -> str:
    s = html.unescape(html.unescape(text)).strip()
    m = DOI_ANCHOR_RE.match(s)
    if m:
        s = m.group(1).strip()
    s = DOI_PREFIX_RE.sub("", s).strip()
    m = DOI_URL_RE.search(s)
    if m:
        s = m.group(1).strip()
    s = s.rstrip(".,;")
    return s


def clean_field_value(field_name: str, text: str) -> str:
    if field_name == "author":
        return _clean_author_value(text)
    if field_name == "doi":
        return _clean_doi_value(text)
    return text


def _looks_like_field_end(text: str, pos: int) -> bool:
    n = len(text)
    k = pos
    while k < n and text[k].isspace():
        k += 1
    if k >= n:
        return True
    if text[k] in ",}":
        return True
    if NEXT_FIELD_RE.match(text[k:]):
        return True
    return False


def _read_braced_value(text: str, start: int, field_name: str = "") -> tuple[str, int]:
    i = start + 1
    depth = 1
    out = []
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            out.append(ch)
            i += 1
            out.append(text[i])
            i += 1
            continue
        if ch == "{":
            depth += 1
            out.append(ch)
            i += 1
            continue
        if ch == "," and COMMA_NEXT_KNOWN_FIELD_RE.match(text[i:]):
            if depth > 1:
                out.append("}" * (depth - 1))
            return "".join(out), i
        if ch == "}":
            if (
                depth == 1
                and field_name in ("author", "editor")
                and not _looks_like_field_end(text, i + 1)
            ):
                out.append("}")
                i += 1
                continue
            depth -= 1
            if depth == 0:
                return "".join(out), i + 1
            out.append(ch)
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out), i


def _read_quoted_value(text: str, start: int) -> tuple[str, int]:
    i = start + 1
    out = []
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            out.append(ch)
            i += 1
            out.append(text[i])
            i += 1
            continue
        if ch == '"':
            return "".join(out), i + 1
        out.append(ch)
        i += 1
    return "".join(out), i


def _read_broken_braced_tail(text: str, start: int) -> tuple[str, int]:
    i = start
    n = len(text)
    while i < n and text[i].isspace():
        i += 1
    if i >= n or text[i] != ",":
        return "", start
    i += 1
    while i < n and text[i].isspace():
        i += 1
    frag_start = i
    while i < n and text[i] not in "}\n\r":
        i += 1
    if i >= n or text[i] != "}":
        return "", start

    frag = text[frag_start:i].strip()
    if not frag or "=" in frag:
        return "", start

    k = i + 1
    while k < n and text[k].isspace():
        k += 1
    if k < n and text[k] not in ",}\n\r":
        return "", start

    return f", {frag}", i + 1


def rewrite_text(text: str) -> tuple[str, int]:
    # Pre-repair malformed name fragments before brace-depth parsing.
    # Without this, patterns like "Kl{\\, Benjamin" can unbalance braces and
    # cause one field to absorb following fields.
    text = BROKEN_NAME_SPLIT_RE.sub(", ", text)

    i = 0
    n = len(text)
    out = []
    changed = 0
    while i < n:
        ch = text[i]
        out.append(ch)
        if ch != "=":
            i += 1
            continue
        field_name = _field_name_before_equals(text, i)

        i += 1
        while i < n and text[i].isspace():
            out.append(text[i])
            i += 1
        if i >= n:
            break

        if text[i] == "{":
            raw, j = _read_braced_value(text, i, field_name)
            raw_initial = raw
            j_initial = j
            recovered_by_known_field_comma = (
                j < n
                and text[j] == ","
                and COMMA_NEXT_KNOWN_FIELD_RE.match(text[j:]) is not None
            )
            tail, j2 = _read_broken_braced_tail(text, j)
            if tail:
                raw = raw + tail
                j = j2
            cleaned = decode_latex_value(raw)
            cleaned = clean_field_value(field_name, cleaned)
            if cleaned != raw_initial or j != j_initial or recovered_by_known_field_comma:
                changed += 1
            out.append("{")
            out.append(cleaned)
            out.append("}")
            i = j
            continue

        if text[i] == '"':
            raw, j = _read_quoted_value(text, i)
            cleaned = decode_latex_value(raw)
            cleaned = clean_field_value(field_name, cleaned)
            if cleaned != raw:
                changed += 1
            out.append('"')
            out.append(cleaned)
            if j <= n and (j == n or text[j - 1] == '"'):
                out.append('"')
            i = j
            continue

        j = i
        while j < n and text[j] not in ",\n\r}":
            j += 1
        raw = text[i:j]
        cleaned = decode_latex_value(raw)
        cleaned = clean_field_value(field_name, cleaned)
        if cleaned != raw:
            changed += 1
        out.append(cleaned)
        i = j

    return "".join(out), changed


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Decode/repair LaTeX in BibTeX field values while preserving field wrappers "
            "(e.g. author={...})."
        )
    )
    parser.add_argument("bib_file", help="Path to .bib file")
    parser.add_argument(
        "-o",
        "--output",
        help="Write rewritten content to this file instead of editing in place",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not write files, only report how many field values would change",
    )
    parser.add_argument(
        "--backup-suffix",
        default=".bak",
        help="Backup suffix for in-place edits (default: .bak)",
    )
    args = parser.parse_args()

    in_path = Path(args.bib_file)
    text = in_path.read_text(encoding="utf-8", errors="replace")
    rewritten, changed = rewrite_text(text)

    print(f"Field values updated: {changed}")
    if args.dry_run or changed == 0:
        return

    if args.output:
        Path(args.output).write_text(rewritten, encoding="utf-8")
        print(f"Written: {args.output}")
        return

    if args.backup_suffix:
        backup_path = in_path.with_name(in_path.name + args.backup_suffix)
        shutil.copy2(in_path, backup_path)
        print(f"Backup: {backup_path}")

    in_path.write_text(rewritten, encoding="utf-8")
    print(f"Updated in place: {in_path}")


if __name__ == "__main__":
    main()
