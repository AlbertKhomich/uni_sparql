#!/usr/bin/env python3
import re
import sys
import html
from collections import Counter
from urllib.parse import quote

ENTRY_START = re.compile(r'(?m)^\s*@\w+\s*[{(]\s*([^,\s]+)\s*,', re.UNICODE)

# DOI extraction/cleanup helpers
DOI_CORE_RE = re.compile(r"10\.\d{4,9}/[^\s\"<>]+", re.IGNORECASE)
LATEX_UNDERSCORE_RE = re.compile(r"\{\\textunderscore\s*\}", re.IGNORECASE)

def strip_html_tags(s: str) -> str:
    # simple tag stripper (good enough for DOI=<a href="...">10....</a>)
    return re.sub(r"<[^>]+>", "", s).strip()

def clean_doi_raw(s: str) -> str:
    """
    Clean a DOI field that may contain:
      - HTML tags
      - HTML entities (sometimes double-escaped)
      - LaTeX underscore (\_ or {\textunderscore})
      - prefixes like doi:, DOI:
      - full URL https://doi.org/...
      - extra notes like (open access)
    """
    s = (s or "").strip()
    if not s:
        return ""

    # Strip HTML tags first (e.g., <a href=...>...</a>)
    s = strip_html_tags(s)

    # HTML-unescape twice to handle '&#38;#60;' patterns
    # 1st: '&#38;#60;' -> '&#60;'
    # 2nd: '&#60;' -> '<'
    s = html.unescape(s)
    s = html.unescape(s)

    # Remove common prefixes / wrappers
    s = re.sub(r"^\s*doi\s*[:=]?\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^doi\.org/", "", s, flags=re.IGNORECASE)

    # Remove common suffix noise
    s = re.sub(r"\(open access\)", "", s, flags=re.IGNORECASE)

    # Fix LaTeX underscore variants
    s = LATEX_UNDERSCORE_RE.sub("_", s)
    s = s.replace(r"\_", "_")

    # Trim / strip trailing punctuation
    s = s.strip().strip(".,;")

    return s

def extract_first_doi(s: str) -> str | None:
    """
    Extract first valid DOI core from a cleaned string.
    Returns lowercase DOI or None.
    """
    s = clean_doi_raw(s)
    if not s:
        return None

    m = DOI_CORE_RE.search(s)
    if not m:
        return None

    doi = m.group(0).strip().strip(".,;").lower()
    return doi or None

def doi_to_url(doi: str) -> str:
    # Percent-encode characters that cannot appear raw in an IRI (e.g. < >)
    # Keep a conservative safe set for common DOI chars.
    safe = "/:._-;()"
    return "https://doi.org/" + quote(doi, safe=safe)

def iter_entries(text: str):
    # split by entry starts: @type{...}
    starts = [m.start() for m in re.finditer(r'(?m)^\s*@\w+\s*[{(]', text)]
    if not starts:
        return
    starts.append(len(text))
    for i in range(len(starts) - 1):
        yield text[starts[i]:starts[i + 1]].strip()

def extract_fields(entry: str):
    """
    Extract fields of the form: name = {value} or name = "value" or name = <value> or bareword
    """
    entry_body = re.sub(r'^\s*@\w+\s*[{(]\s*[^,]+,\s*', '', entry, flags=re.UNICODE)

    field_re = re.compile(
        r'(?is)([A-Za-z][A-Za-z0-9_:-]*)\s*=\s*'
        r'(\{.*?\}|"[^"]*"|<.*?>|[^,}]+)\s*(?:,|}$)',
        re.UNICODE
    )

    fields = []
    for m in field_re.finditer(entry_body):
        name = m.group(1)
        raw = m.group(2).strip()

        # unwrap { } or " "
        if (raw.startswith("{") and raw.endswith("}")) or (raw.startswith('"') and raw.endswith('"')):
            raw = raw[1:-1].strip()

        fields.append((name, raw))
    return fields

def main(path: str):
    text = open(path, "r", encoding="utf-8", errors="replace").read()

    keys = []
    field_counts = Counter()
    doi_samples = []  # show a few parsed DOIs for sanity

    for entry in iter_entries(text):
        m = ENTRY_START.search(entry)
        if not m:
            continue
        key = m.group(1)
        keys.append(key)

        fields = extract_fields(entry)
        for name, value in fields:
            lname = name.lower()
            field_counts[lname] += 1

            if lname == "doi" and len(doi_samples) < 15:
                doi = extract_first_doi(value)
                cleaned_raw = clean_doi_raw(value)
                if doi:
                    doi_samples.append((key, doi, doi_to_url(doi), cleaned_raw))
                else:
                    doi_samples.append((key, None, None, cleaned_raw))

    print("=== Citation keys (unique, sorted) ===")
    for k in sorted(set(keys)):
        print(k)
    print(f"\nTotal entries: {len(keys)} (unique keys: {len(set(keys))})")

    print("\n=== Field names (unique) ===")
    for f in sorted(field_counts.keys()):
        print(f)

    print("\n=== Field name counts ===")
    for f, c in field_counts.most_common():
        print(f"{f}\t{c}")

    if doi_samples:
        print("\n=== Sample DOI parsing (first 15 entries with DOI field) ===")
        print("key\tdoi_extracted\tdoi_url\tcleaned_raw")
        for k, doi, url, raw in doi_samples:
            print(f"{k}\t{doi or ''}\t{url or ''}\t{raw}")

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python3 bib_list_keys.py your.bib", file=sys.stderr)
        sys.exit(2)
    main(sys.argv[1])
