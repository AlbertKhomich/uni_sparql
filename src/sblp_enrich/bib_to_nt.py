#!/usr/bin/env python3
"""
bib_to_nt.py

Convert a .bib (BibTeX/BibLaTeX) file to N-Triples (.nt) following your scheme.

Field set handled (your list):
address, author, booktitle, collection, doi, editor, journal,
location, number, pages, publisher, series, title, volume, year

Adds:
- LaTeX -> Unicode decoding via pylatexenc (recommended)
- name list splitting primarily on 'and' (biber-friendly), with safe fallbacks for ';', ' / ', '&'
- optional heuristic for broken "Last, F., Other, G." strings (can be disabled)

Usage:
  python3 bib_to_nt.py input.bib output.nt --base https://dice-research.org/id
"""

import argparse
import hashlib
import re
import html
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

import bibtexparser
from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import RDF, XSD

from pylatexenc.latex2text import LatexNodes2Text

SCHEMA = Namespace("https://schema.org/")

TAG_RE = re.compile(r"<[^>]+>")
DOI_CORE_RE = re.compile(r'10\.\d{4,9}/[^\s"]+', re.IGNORECASE)
LATEX_UNDERSCORE_RE = re.compile(r"\{\\textunderscore\s*\}", re.IGNORECASE)

# Guard against accidentally splitting institutions into "people"
ORG_HINT_RE = re.compile(
    r"\b(universit[aä]t|university|institut|institute|department|faculty|press|verlag|gmbh|ag|inc\.|ltd\.|llc)\b",
    re.IGNORECASE,
)

_l2t = LatexNodes2Text()  # reuse for speed


def strip_html(s: str) -> str:
    return TAG_RE.sub("", s).strip()


def norm_space(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip())


def hash12(s: str) -> str:
    s = norm_space(s).lower()
    return hashlib.sha1(s.encode("utf-8", "ignore")).hexdigest()[:12]


def decode_latex(s: Optional[str]) -> Optional[str]:
    """
    Decode LaTeX-ish content into Unicode using pylatexenc.
    Also handles double HTML unescape (common in exports).
    """
    if s is None:
        return None
    t = str(s)

    # Some exports contain HTML entities (sometimes double-encoded)
    t = html.unescape(html.unescape(t))

    # Strip accidental HTML tags like <a href=...>doi</a>
    t = strip_html(t)

    # pylatexenc handles accents/macros/braces fairly well
    try:
        t = _l2t.latex_to_text(t)
    except Exception:
        # If decoding fails for some weird fragment, keep original text
        pass

    return norm_space(t) or None


def clean_doi_text(raw: str) -> str:
    s = strip_html(raw).strip()

    # HTML-unescape twice to handle patterns like '&#38;#60;' -> '&#60;' -> '<'
    s = html.unescape(s)
    s = html.unescape(s)

    # remove common wrappers/noise
    s = re.sub(r"^\s*(doi\s*[:=]?\s*)", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^doi\.org/", "", s, flags=re.IGNORECASE)

    # fix underscore encodings
    s = LATEX_UNDERSCORE_RE.sub("_", s)
    s = s.replace(r"\_", "_")

    # remove common suffix noise
    s = re.sub(r"\(open access\)", "", s, flags=re.IGNORECASE)

    return s.strip()


def extract_first_doi(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None

    s = clean_doi_text(str(raw))
    m = DOI_CORE_RE.search(s)
    if not m:
        return None

    doi = m.group(0).strip().strip(".,;")
    doi = re.sub(r"\s+", "", doi)
    return doi.lower() or None


def doi_iri(doi: str) -> str:
    """
    Percent-encode characters outside a conservative safe set.
    Encodes '<' and '>' => valid IRI.
    """
    safe = "/:._-;()"
    return "https://doi.org/" + quote(doi, safe=safe)


def entry_lower_map(e: Dict[str, Any]) -> Dict[str, Any]:
    return {str(k).lower(): v for k, v in e.items()}


def get_any(e_lc: Dict[str, Any], *names: str) -> Optional[Any]:
    for n in names:
        v = e_lc.get(n.lower())
        if v is not None:
            return v
    return None


def split_people(field: Optional[str], allow_comma_paired: bool = True, decode: bool = True) -> List[str]:
    """
    Primary separator: 'and' (correct for bibtex/biblatex/biber).
    Also tolerates ';', ' / ', '&' as separators from broken exports.
    Optionally splits the broken pattern 'Last, F., Other, G.' into two people.
    """
    if not field:
        return []

    t = norm_space(str(field))

    # tolerate clear separators
    t = re.sub(r"\s*;\s*", " and ", t)
    t = re.sub(r"\s+/\s+", " and ", t)
    t = re.sub(r"\s*&\s*", " and ", t)

    parts = [norm_space(p) for p in re.split(r"\s+\band\b\s+", t, flags=re.IGNORECASE) if norm_space(p)]

    out: List[str] = []
    for part in parts:
        if not allow_comma_paired:
            out.append(part)
            continue

        if ORG_HINT_RE.search(part):
            out.append(part)
            continue

        segs = [norm_space(x) for x in part.split(",")]
        # Heuristic: even number of segments >= 4 and initials are present in given-name slots
        if len(segs) >= 4 and (len(segs) % 2) == 0 and any("." in g for g in segs[1::2]):
            for i in range(0, len(segs), 2):
                out.append(norm_space(f"{segs[i]}, {segs[i+1]}"))
        else:
            out.append(part)

    if decode:
        out = [decode_latex(x) or x for x in out]

    return [norm_space(x) for x in out if norm_space(x)]


def parse_pages(pages: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    if not pages:
        return None, None
    s = norm_space(str(pages))
    s2 = s.replace("--", "-").replace("–", "-").replace("—", "-")
    if "-" not in s2:
        return s2, None
    a, b = s2.split("-", 1)
    a, b = a.strip(), b.strip()
    return (a or None), (b or None)


def maybe_num_pages(start: Optional[str], end: Optional[str]) -> Optional[int]:
    if not start or not end:
        return None
    if not start.isdigit() or not end.isdigit():
        return None
    s, e = int(start), int(end)
    if e < s:
        return None
    return e - s + 1


def mint_work_uri(base: str, bibkey: Optional[str], doi: Optional[str], title: Optional[str], year: Optional[str]) -> URIRef:
    base = base.rstrip("/")

    if doi:
        return URIRef(f"{base}/publication/ris/doi/{quote(doi, safe='')}")
    if bibkey:
        return URIRef(f"{base}/publication/ris/bib/{quote(str(bibkey), safe='')}")
    seed = f"{norm_space(title or '')}||{norm_space(year or '')}"
    return URIRef(f"{base}/publication/ris/hash/{hash12(seed)}")


def mint_person_uri(base: str, name: str) -> URIRef:
    return URIRef(f"{base.rstrip('/')}/person/hash/{hash12(name)}")


def mint_org_uri(base: str, name: str) -> URIRef:
    return URIRef(f"{base.rstrip('/')}/org/{hash12(name)}")


def mint_venue_uri(base: str, name: str) -> URIRef:
    return URIRef(f"{base.rstrip('/')}/venue/{hash12(name)}")


def add_lit(g: Graph, s: URIRef, p: URIRef, v: Optional[str], datatype: Optional[URIRef] = None):
    if v is None:
        return
    v = str(v).strip()
    if not v:
        return
    if datatype is None:
        g.add((s, p, Literal(v)))
    else:
        g.add((s, p, Literal(v, datatype=datatype)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input_bib", help="Input .bib file")
    ap.add_argument("output_nt", help="Output .nt file")
    ap.add_argument("--base", default="https://dice-research.org/id", help="Base URI for minted resources")
    ap.add_argument("--no-comma-pair-split", action="store_true",
                    help="Disable heuristic splitting of 'Last, F., Other, G.' into multiple people")
    ap.add_argument("--keep-latex", action="store_true",
                    help="Do NOT decode LaTeX to Unicode in output literals (and hashing)")
    args = ap.parse_args()

    allow_comma_paired = not args.no_comma_pair_split
    do_decode = not args.keep_latex

    with open(args.input_bib, "r", encoding="utf-8", errors="replace") as f:
        bib = bibtexparser.load(f)

    g = Graph()
    g.bind("schema", SCHEMA)

    n_entries = 0
    n_doi_ok = 0
    n_doi_raw = 0

    for e in getattr(bib, "entries", []):
        if not isinstance(e, dict):
            continue

        n_entries += 1
        e_lc = entry_lower_map(e)

        bibkey = get_any(e_lc, "id")
        title_raw = get_any(e_lc, "title")
        title = decode_latex(title_raw) if (do_decode and title_raw) else (norm_space(str(title_raw)) if title_raw else None)

        year = get_any(e_lc, "year")
        year = str(year).strip() if year else None

        doi_raw = get_any(e_lc, "doi")
        doi = extract_first_doi(str(doi_raw)) if doi_raw else None

        work = mint_work_uri(args.base, str(bibkey) if bibkey else None, doi, title, year)

        g.add((work, RDF.type, SCHEMA.CreativeWork))
        add_lit(g, work, SCHEMA.name, title)

        if year:
            add_lit(g, work, SCHEMA.datePublished, year)

        if bibkey:
            add_lit(g, work, SCHEMA.identifier, f"bibtex:{bibkey}")

        if doi:
            n_doi_ok += 1
            add_lit(g, work, SCHEMA.identifier, f"DOI:{doi}")
            g.add((work, SCHEMA.sameAs, URIRef(doi_iri(doi))))
        else:
            if doi_raw:
                raw_clean = strip_html(str(doi_raw)).strip()
                raw_clean = html.unescape(html.unescape(raw_clean))
                if raw_clean:
                    n_doi_raw += 1
                    add_lit(g, work, SCHEMA.identifier, f"DOI_RAW:{raw_clean}")

        add_lit(g, work, SCHEMA.volumeNumber, get_any(e_lc, "volume"))
        add_lit(g, work, SCHEMA.issueNumber, get_any(e_lc, "number"))

        ps, pe = parse_pages(get_any(e_lc, "pages"))
        add_lit(g, work, SCHEMA.pageStart, ps)
        add_lit(g, work, SCHEMA.pageEnd, pe)
        np = maybe_num_pages(ps, pe)
        if np is not None:
            g.add((work, SCHEMA.numberOfPages, Literal(str(np), datatype=XSD.integer)))

        venue_raw = get_any(e_lc, "journal", "booktitle")
        if venue_raw:
            venue_name = decode_latex(venue_raw) if do_decode else norm_space(str(venue_raw))
            if venue_name:
                venue = mint_venue_uri(args.base, venue_name)
                g.add((venue, RDF.type, SCHEMA.CreativeWork))
                add_lit(g, venue, SCHEMA.name, venue_name)
                g.add((work, SCHEMA.isPartOf, venue))

        pub_raw = get_any(e_lc, "publisher")
        if pub_raw:
            pub_name = decode_latex(pub_raw) if do_decode else norm_space(str(pub_raw))
            if pub_name:
                org = mint_org_uri(args.base, pub_name)
                g.add((org, RDF.type, SCHEMA.Organization))
                add_lit(g, org, SCHEMA.name, pub_name)
                g.add((work, SCHEMA.publisher, org))

        author_raw = get_any(e_lc, "author")
        for aname in split_people(str(author_raw) if author_raw else None,
                                  allow_comma_paired=allow_comma_paired, decode=do_decode):
            if aname.lower().startswith("https://orcid.org/"):
                g.add((work, SCHEMA.author, URIRef(aname)))
                continue
            person = mint_person_uri(args.base, aname)
            g.add((person, RDF.type, SCHEMA.Person))
            add_lit(g, person, SCHEMA.name, aname)
            g.add((work, SCHEMA.author, person))

        editor_raw = get_any(e_lc, "editor")
        for ename in split_people(str(editor_raw) if editor_raw else None,
                                  allow_comma_paired=allow_comma_paired, decode=do_decode):
            if ename.lower().startswith("https://orcid.org/"):
                g.add((work, SCHEMA.editor, URIRef(ename)))
                continue
            person = mint_person_uri(args.base, ename)
            g.add((person, RDF.type, SCHEMA.Person))
            add_lit(g, person, SCHEMA.name, ename)
            g.add((work, SCHEMA.editor, person))

        place_raw = get_any(e_lc, "location", "address")
        if place_raw:
            place = decode_latex(place_raw) if do_decode else norm_space(str(place_raw))
            add_lit(g, work, SCHEMA.location, place)

        for extra_raw in [get_any(e_lc, "series"), get_any(e_lc, "collection")]:
            if extra_raw:
                extra_name = decode_latex(extra_raw) if do_decode else norm_space(str(extra_raw))
                if extra_name:
                    extra_node = mint_venue_uri(args.base, extra_name)
                    g.add((extra_node, RDF.type, SCHEMA.CreativeWork))
                    add_lit(g, extra_node, SCHEMA.name, extra_name)
                    g.add((work, SCHEMA.isPartOf, extra_node))

    g.serialize(destination=args.output_nt, format="nt")
    print(f"Wrote {len(g)} triples to {args.output_nt}")
    print(f"Entries: {n_entries} | DOI ok: {n_doi_ok} | DOI raw kept: {n_doi_raw}")


if __name__ == "__main__":
    main()
