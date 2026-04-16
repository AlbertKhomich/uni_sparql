#!/usr/bin/env python3
"""
bib_repair_all.py
Best-effort BibTeX repair: structural recovery + value cleanup + canonical re-serialization.
"""

from __future__ import annotations

import argparse
import html
import re
import shutil
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Tuple, Dict, Optional

try:
    from pylatexenc.latex2text import LatexNodes2Text  # type: ignore
except Exception:
    LatexNodes2Text = None


# ---------------------------
# Value decoding / cleaning
# ---------------------------

_l2t = LatexNodes2Text() if LatexNodes2Text else None

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
    r"\{?\s*(?:\\\s*)+([\"'`~^cv’´])\s*(?:\{\s*(\\i|\\j|[^\W\d_])\s*\}|(\\i|\\j|[^\W\d_]))\s*\}?",
    re.UNICODE,
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
SPECIAL_CMD_RE = re.compile(r"\{?\s*(?:\\\s*)+(ss|SS|ae|AE|oe|OE|aa|AA|o|O|l|L|i|j)\s*\}?", re.UNICODE)

SPACING_CMD_RE = re.compile(r"\\\s+|\\[,;:!]")
ESCAPED_PUNCT_RE = re.compile(r"\\([&#%_{}])")
SINGLE_BRACE_RE = re.compile(r"\{([^{}\s])\}")
MULTI_SPACE_RE = re.compile(r"\s{2,}")
SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([,;:.])")

HTML_TAG_RE = re.compile(r"<[^>]+>")

DOI_ANCHOR_RE = re.compile(r"^\s*<a\b[^>]*>\s*([^<]+?)\s*</a>\s*$", re.IGNORECASE)
DOI_URL_RE = re.compile(r"https?://(?:dx\.)?doi\.org/(10\.[^ \t\r\n<>\"}]+)", re.IGNORECASE)
DOI_PREFIX_RE = re.compile(r"^\s*doi:\s*", re.IGNORECASE)

PAGES_DASH_RE = re.compile(r"\s*([–—−-])\s*")  # en dash, em dash, minus, hyphen


def _apply_accent(mark_cmd: str, base: str) -> str:
    mark = ACCENT_TO_COMBINING.get(mark_cmd)
    if mark is None:
        return "\\" + mark_cmd + base
    return unicodedata.normalize("NFC", base + mark)


def _convert_latex_accents(text: str) -> str:
    def repl(m: re.Match[str]) -> str:
        base = m.group(2) or m.group(3)
        if not base:
            return m.group(0)
        base = base.strip()
        if base in ("\\i", "i", "ı"):
            base = "i"
        elif base in ("\\j", "j"):
            base = "j"
        elif len(base) != 1 or not base.isalpha():
            return m.group(0)
        return _apply_accent(m.group(1), base)

    s = text
    for _ in range(3):
        ns = ACCENT_RE.sub(repl, s)
        if ns == s:
            break
        s = ns
    return s


def _convert_special_commands(text: str) -> str:
    def repl(m: re.Match[str]) -> str:
        cmd = m.group(1)
        return SPECIAL_CMD_MAP.get(cmd, m.group(0))

    s = text
    for _ in range(3):
        ns = SPECIAL_CMD_RE.sub(repl, s)
        if ns == s:
            break
        s = ns
    return s


def decode_latex_value(text: str, *, strip_html_tags: bool = True) -> str:
    s = html.unescape(html.unescape(text))

    if strip_html_tags and "<" in s and ">" in s:
        s = HTML_TAG_RE.sub("", s)

    if _l2t is not None:
        try:
            s = _l2t.latex_to_text(s)
        except Exception:
            pass

    s = _convert_latex_accents(s)
    s = _convert_special_commands(s)
    s = SPACING_CMD_RE.sub(" ", s)
    s = ESCAPED_PUNCT_RE.sub(r"\1", s)

    for _ in range(4):
        ns = SINGLE_BRACE_RE.sub(r"\1", s).replace("{}", "")
        if ns == s:
            break
        s = ns

    s = SPACE_BEFORE_PUNCT_RE.sub(r"\1", s)
    s = MULTI_SPACE_RE.sub(" ", s).strip()
    return s


def clean_doi(text: str) -> str:
    s = html.unescape(html.unescape(text)).strip()
    m = DOI_ANCHOR_RE.match(s)
    if m:
        s = m.group(1).strip()
    s = DOI_PREFIX_RE.sub("", s).strip()
    m = DOI_URL_RE.search(s)
    if m:
        s = m.group(1).strip()
    s = s.strip().rstrip(".,;")
    return s


def clean_pages(text: str) -> str:
    s = text.strip()
    # normalize ranges like 384–399 or 384 - 399 -> 384--399
    # only if it looks like a numeric range
    if re.search(r"\d", s):
        s2 = PAGES_DASH_RE.sub("--", s)
        # collapse accidental multiple dashes
        s2 = re.sub(r"-{3,}", "--", s2)
        s = s2
    s = MULTI_SPACE_RE.sub(" ", s).strip()
    return s


def clean_field_value(field: str, value: str) -> str:
    f = field.lower()
    if f == "doi":
        return clean_doi(value)
    if f == "pages":
        return clean_pages(value)
    # authors/editors: tidy spacing (don't over-do here)
    if f in ("author", "editor"):
        v = value
        v = SPACE_BEFORE_PUNCT_RE.sub(r"\1", v)
        v = MULTI_SPACE_RE.sub(" ", v).strip()
        return v
    return value


# ---------------------------
# BibTeX parsing + repair
# ---------------------------

ENTRY_START_RE = re.compile(r"(?m)^[ \t]*@")

FIELD_NAME_RE = re.compile(r"[A-Za-z_][\w-]*")
FIELD_START_AHEAD_RE = re.compile(r"\s*,?\s*[A-Za-z_][\w-]*\s*=")
COMMA_FIELD_START_RE = re.compile(r",\s*[A-Za-z_][\w-]*\s*=")


def _looks_like_field_end(text: str, pos: int) -> bool:
    i = pos
    n = len(text)
    while i < n and text[i].isspace():
        i += 1
    if i >= n:
        return True
    if text[i] in ",}":
        return True
    if FIELD_START_AHEAD_RE.match(text[i:]):
        return True
    return False


def _read_braced_value(text: str, start: int) -> Tuple[str, int, bool]:
    """
    Reads from text[start] == '{' (not including the outer braces in returned value).
    Error recovery:
      - if '}' would close but next doesn't look like field end, treat it as literal and continue
      - if we see ', field=' while still at depth==1, treat as missing closing brace and end there
    Returns (raw_value, new_pos, recovered_flag)
    """
    assert text[start] == "{"
    i = start + 1
    depth = 1
    out: List[str] = []
    recovered = False
    n = len(text)

    while i < n:
        ch = text[i]

        if ch == "\\" and i + 1 < n:
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

        if ch == "," and depth == 1 and COMMA_FIELD_START_RE.match(text[i:]):
            recovered = True
            return "".join(out), i, recovered

        if ch == "}":
            depth -= 1
            if depth == 0:
                if not _looks_like_field_end(text, i + 1):
                    # premature close -> keep as literal
                    recovered = True
                    out.append("}")
                    depth = 1
                    i += 1
                    continue
                return "".join(out), i + 1, recovered
            out.append("}")
            i += 1
            continue

        out.append(ch)
        i += 1

    recovered = True
    return "".join(out), i, recovered


def _read_quoted_value(text: str, start: int) -> Tuple[str, int, bool]:
    """
    Reads from text[start] == '"'.
    Error recovery: if a quote ends but next doesn't look like field end, treat it as literal and continue.
    Returns (raw_value, new_pos, recovered_flag).
    """
    assert text[start] == '"'
    i = start + 1
    out: List[str] = []
    recovered = False
    n = len(text)

    while i < n:
        ch = text[i]

        if ch == "\\" and i + 1 < n:
            out.append(ch)
            i += 1
            out.append(text[i])
            i += 1
            continue

        if ch == '"':
            if not _looks_like_field_end(text, i + 1):
                recovered = True
                out.append('"')
                i += 1
                continue
            return "".join(out), i + 1, recovered

        out.append(ch)
        i += 1

    recovered = True
    return "".join(out), i, recovered


def _read_bare_value(text: str, start: int) -> Tuple[str, int]:
    i = start
    n = len(text)
    while i < n and text[i] not in ",\n\r}":
        i += 1
    return text[start:i].strip(), i


def _skip_ws_and_commas(text: str, i: int) -> int:
    n = len(text)
    while i < n:
        if text[i].isspace() or text[i] == ",":
            i += 1
            continue
        break
    return i


@dataclass
class BibEntry:
    entry_type: str
    key: str
    fields: List[Tuple[str, str]]  # ordered
    recovered: bool
    raw_type_case: str = ""


def parse_entry_content(entry_type: str, content: str) -> BibEntry:
    """
    content is inside the outer braces/parentheses.
    Expected: key, field=..., field=..., ...
    Best-effort: missing comma after key, missing commas between fields, etc.
    """
    recovered = False

    s = content.strip()
    if not s:
        return BibEntry(entry_type=entry_type, key="", fields=[], recovered=True)

    # Parse key up to first comma at top-level
    # (keys shouldn't contain braces/quotes, so we keep it simple)
    comma = s.find(",")
    if comma == -1:
        key = s.strip()
        body = ""
        recovered = True
    else:
        key = s[:comma].strip()
        body = s[comma + 1 :]

    fields: List[Tuple[str, str]] = []

    i = 0
    n = len(body)

    while True:
        i = _skip_ws_and_commas(body, i)
        if i >= n:
            break
        if body[i] == "}":
            break

        m = FIELD_NAME_RE.match(body[i:])
        if not m:
            # skip junk until next comma or end
            recovered = True
            j = i + 1
            while j < n and body[j] not in ",\n\r":
                j += 1
            i = j + 1
            continue

        name = m.group(0)
        i += len(name)

        # skip ws
        while i < n and body[i].isspace():
            i += 1
        if i >= n or body[i] != "=":
            recovered = True
            # try to resync: look ahead for '='
            eq = body.find("=", i)
            if eq == -1:
                break
            i = eq

        # consume '='
        i += 1
        while i < n and body[i].isspace():
            i += 1
        if i >= n:
            recovered = True
            break

        # read value
        if body[i] == "{":
            raw, j, rec = _read_braced_value(body, i)
            recovered |= rec
            i = j
        elif body[i] == '"':
            raw, j, rec = _read_quoted_value(body, i)
            recovered |= rec
            i = j
        else:
            raw, j = _read_bare_value(body, i)
            i = j

        # Repair missing comma between fields:
        # after reading a value, if next looks like "field=", accept it even without comma
        k = i
        while k < n and body[k].isspace():
            k += 1
        if k < n and body[k] not in ",}" and FIELD_START_AHEAD_RE.match(body[k:]):
            recovered = True
            i = k  # continue as if comma existed

        # decode + clean
        decoded = decode_latex_value(raw)
        cleaned = clean_field_value(name, decoded)

        fields.append((name.lower(), cleaned))

    return BibEntry(entry_type=entry_type.lower(), key=key, fields=fields, recovered=recovered)


def extract_entries(text: str) -> Iterable[Tuple[str, str]]:
    """
    Yields ("text", raw) or ("entry", entry_raw_text)
    Uses '@' at start of line to avoid catching emails/urls.
    """
    starts = [m.start() for m in ENTRY_START_RE.finditer(text)]
    if not starts:
        yield ("text", text)
        return

    n = len(text)
    for idx, st in enumerate(starts):
        prev_end = starts[idx - 1] if idx > 0 else None
        if idx == 0 and st > 0:
            yield ("text", text[:st])

        # determine a safe scan limit: next entry start (if current entry is broken)
        next_st = starts[idx + 1] if idx + 1 < len(starts) else n
        entry_raw, end_pos = _scan_one_entry(text, st, next_st)
        yield ("entry", entry_raw)

        # emit in-between text (whitespace/comments) if any
        if end_pos < next_st:
            yield ("text", text[end_pos:next_st])


def _scan_one_entry(text: str, start: int, fallback_end: int) -> Tuple[str, int]:
    """
    Best-effort scan for one entry from '@' to matching outer brace/paren.
    If not found before fallback_end, cut there and mark as unterminated.
    Returns (entry_text, end_pos_in_original_text).
    """
    i = start
    n = len(text)

    # skip leading spaces
    while i < n and text[i].isspace():
        i += 1
    if i >= n or text[i] != "@":
        return text[start:fallback_end], fallback_end

    i += 1
    # read entry type
    j = i
    while j < n and (text[j].isalpha() or text[j] in "@"):
        j += 1
    entry_type = text[i:j].strip()
    i = j

    # skip ws
    while i < n and text[i].isspace():
        i += 1
    if i >= n or text[i] not in "{(":
        return text[start:fallback_end], fallback_end

    open_ch = text[i]
    close_ch = "}" if open_ch == "{" else ")"
    i += 1

    depth = 1
    while i < n and i < fallback_end:
        ch = text[i]
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return text[start : i + 1], i + 1
        i += 1

    # Unterminated: return up to fallback_end (we'll close it later in serialization)
    return text[start:fallback_end], fallback_end


def parse_raw_entry(entry_raw: str) -> Tuple[Optional[BibEntry], bool]:
    """
    Parses raw '@type{...}' or '@type(...)'.
    Returns (BibEntry or None if can't parse), unterminated_flag.
    """
    m = re.match(r"(?s)^\s*@\s*([A-Za-z]+)\s*([\{\(])", entry_raw)
    if not m:
        return None, False

    entry_type = m.group(1)
    opener = m.group(2)
    closer = "}" if opener == "{" else ")"

    # find matching closer for outer wrapper, best effort
    # if the raw ends without closer, mark unterminated
    unterminated = not entry_raw.rstrip().endswith(closer)

    # get content between first opener and last closer if present
    first = entry_raw.find(opener)
    last = entry_raw.rfind(closer)
    if first == -1:
        return None, unterminated
    if last == -1 or last <= first:
        content = entry_raw[first + 1 :]
        unterminated = True
    else:
        content = entry_raw[first + 1 : last]

    entry = parse_entry_content(entry_type, content)
    entry.raw_type_case = entry_type
    entry.recovered |= unterminated
    return entry, unterminated


def serialize_entry(e: BibEntry) -> str:
    # Canonical output: @type{key, \n  field={...},\n}
    lines = []
    et = e.entry_type.lower()
    key = e.key.strip()
    lines.append(f"@{et}{{{key},")
    for name, value in e.fields:
        # Always brace-wrap for stability
        v = value.strip()
        lines.append(f"  {name}={{{v}}},")
    lines.append("}")
    return "\n".join(lines)


def repair_bibtex(text: str) -> Tuple[str, Dict[str, int]]:
    out: List[str] = []
    stats = {
        "entries_total": 0,
        "entries_recovered": 0,
        "entries_unparsed": 0,
        "fields_total": 0,
    }

    for kind, chunk in extract_entries(text):
        if kind == "text":
            out.append(chunk)
            continue

        stats["entries_total"] += 1
        entry, _unter = parse_raw_entry(chunk)
        if entry is None:
            stats["entries_unparsed"] += 1
            out.append(chunk)
            continue

        stats["fields_total"] += len(entry.fields)
        if entry.recovered:
            stats["entries_recovered"] += 1

        # Replace raw entry with repaired canonical one, keep surrounding newlines stable
        repaired = serialize_entry(entry)
        # Ensure it ends with a newline if original had it
        if chunk.endswith("\n") and not repaired.endswith("\n"):
            repaired += "\n"
        out.append(repaired)

    return "".join(out), stats


# ---------------------------
# CLI
# ---------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="Repair BibTeX structure + clean all fields (best-effort).")
    ap.add_argument("bib_file", help="Path to .bib file")
    ap.add_argument("-o", "--output", help="Write to output file instead of editing in place")
    ap.add_argument("--dry-run", action="store_true", help="Do not write, only report stats")
    ap.add_argument("--backup-suffix", default=".bak", help="Backup suffix for in-place edits (default: .bak)")
    args = ap.parse_args()

    in_path = Path(args.bib_file)
    text = in_path.read_text(encoding="utf-8", errors="replace")

    repaired, stats = repair_bibtex(text)

    print(
        "Entries: {entries_total} | Recovered: {entries_recovered} | Unparsed left as-is: {entries_unparsed} | Fields: {fields_total}".format(
            **stats
        )
    )

    if args.dry_run:
        return

    if args.output:
        Path(args.output).write_text(repaired, encoding="utf-8")
        print(f"Written: {args.output}")
        return

    if args.backup_suffix:
        backup_path = in_path.with_name(in_path.name + args.backup_suffix)
        shutil.copy2(in_path, backup_path)
        print(f"Backup: {backup_path}")

    in_path.write_text(repaired, encoding="utf-8")
    print(f"Updated in place: {in_path}")


if __name__ == "__main__":
    main()
