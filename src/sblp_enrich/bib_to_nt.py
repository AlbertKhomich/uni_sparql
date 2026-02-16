#!/usr/bin/env python3
"""
bib_to_nt.py

Convert a .bib (BibTeX) file to N-Triples (.nt) following your existing scheme-ish:

Work:
  a schema:CreativeWork
  schema:name
  schema:datePublished
  schema:identifier  "DOI:..."  (+ "bibtex:<key>")
  schema:sameAs      <https://doi.org/...>   (only when DOI is valid/clean)
  schema:volumeNumber / schema:issueNumber
  schema:pageStart / schema:pageEnd / schema:numberOfPages (when numeric)
  schema:isPartOf    <.../id/venue/...>
  schema:publisher   <.../id/org/...>
  schema:author / schema:editor  IRIs (local hash) (or ORCID IRIs if provided)

People/Org/Venue:
  a schema:Person / schema:Organization / schema:CreativeWork
  schema:name "..."

Usage:
  python3 bib_to_nt.py input.bib output.nt --base https://dice-research.org/id
"""

import argparse
import hashlib
import re
import html
from urllib.parse import quote

import bibtexparser
from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import RDF, XSD

SCHEMA = Namespace("https://schema.org/")

TAG_RE = re.compile(r"<[^>]+>")  # strip <a ...>...</a>

# DOI extraction/cleanup (robust against RIS/BibTeX junk)
# IMPORTANT: allow angle brackets in DOI core (old SICI-style DOIs contain <...>)
DOI_CORE_RE = re.compile(r'10\.\d{4,9}/[^\s"]+', re.IGNORECASE)
LATEX_UNDERSCORE_RE = re.compile(r"\{\\textunderscore\s*\}", re.IGNORECASE)

def strip_html(s: str) -> str:
    return TAG_RE.sub("", s).strip()

def norm_space(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip())

def hash12(s: str) -> str:
    s = norm_space(s).lower()
    return hashlib.sha1(s.encode("utf-8", "ignore")).hexdigest()[:12]

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

def extract_first_doi(raw: str | None) -> str | None:
    """
    Return normalized DOI (lowercase) if we can find one, else None.
    Handles messy exports with LaTeX/extra text/multiple DOIs.
    Rejects non-DOI garbage.
    """
    if not raw:
        return None

    s = clean_doi_text(raw)

    # Extract the first DOI-looking substring from messy strings.
    m = DOI_CORE_RE.search(s)
    if not m:
        return None

    doi = m.group(0).strip().strip(".,;")

    # remove any stray whitespace inside (some exports insert spaces)
    doi = re.sub(r"\s+", "", doi)

    return doi.lower() or None

def doi_iri(doi: str) -> str:
    """
    Build a safe DOI URL IRI by percent-encoding characters outside a conservative safe set.
    This will encode '<' and '>' into %3C and %3E, which is required for valid IRIs.
    """
    safe = "/:._-;()"
    return "https://doi.org/" + quote(doi, safe=safe)

def split_people(field: str | None) -> list[str]:
    if not field:
        return []
    parts = re.split(r"\s+\band\b\s+", field.strip())
    return [norm_space(p) for p in parts if norm_space(p)]

def parse_pages(pages: str | None) -> tuple[str | None, str | None]:
    if not pages:
        return None, None
    s = norm_space(pages)
    s2 = s.replace("--", "-").replace("–", "-").replace("—", "-")
    if "-" not in s2:
        return s2, None
    a, b = s2.split("-", 1)
    a, b = a.strip(), b.strip()
    return (a or None), (b or None)

def maybe_num_pages(start: str | None, end: str | None) -> int | None:
    if not start or not end:
        return None
    if not start.isdigit() or not end.isdigit():
        return None
    s, e = int(start), int(end)
    if e < s:
        return None
    return e - s + 1

def mint_work_uri(base: str, bibkey: str | None, doi: str | None, title: str | None, year: str | None) -> URIRef:
    """
    Mint publication URIs like:
      https://dice-research.org/id/publication/ris/doi/<doi-encoded>
      https://dice-research.org/id/publication/ris/bib/<bibkey-encoded>
      https://dice-research.org/id/publication/ris/hash/<hash>
    """
    base = base.rstrip("/")

    if doi:
        # make it safe and stable
        return URIRef(f"{base}/publication/ris/doi/{quote(doi, safe='')}")

    if bibkey:
        # keep unicode but url-encode it (so Cyrillic keys etc won't break)
        return URIRef(f"{base}/publication/ris/bib/{quote(bibkey, safe='')}")

    seed = f"{norm_space(title or '')}||{norm_space(year or '')}"
    return URIRef(f"{base}/publication/ris/hash/{hash12(seed)}")


def mint_person_uri(base: str, name: str) -> URIRef:
    return URIRef(f"{base}/person/hash/{hash12(name)}")

def mint_org_uri(base: str, name: str) -> URIRef:
    return URIRef(f"{base}/org/{hash12(name)}")

def mint_venue_uri(base: str, name: str) -> URIRef:
    return URIRef(f"{base}/venue/{hash12(name)}")

def add_lit(g: Graph, s: URIRef, p: URIRef, v: str | None, datatype=None):
    if v is None:
        return
    v = v.strip()
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
    args = ap.parse_args()

    with open(args.input_bib, "r", encoding="utf-8", errors="replace") as f:
        bib = bibtexparser.load(f)

    g = Graph()
    g.bind("schema", SCHEMA)

    n_entries = 0
    n_doi_ok = 0
    n_doi_raw = 0

    for e in bib.entries:
        n_entries += 1

        bibkey = e.get("ID")
        title = e.get("title")
        year = (e.get("year") or "").strip() or None

        doi_raw = e.get("DOI") or e.get("doi")
        doi = extract_first_doi(doi_raw)

        work = mint_work_uri(args.base, bibkey, doi, title, year)

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

        add_lit(g, work, SCHEMA.volumeNumber, e.get("volume"))
        add_lit(g, work, SCHEMA.issueNumber, e.get("number"))

        ps, pe = parse_pages(e.get("pages"))
        add_lit(g, work, SCHEMA.pageStart, ps)
        add_lit(g, work, SCHEMA.pageEnd, pe)
        np = maybe_num_pages(ps, pe)
        if np is not None:
            g.add((work, SCHEMA.numberOfPages, Literal(str(np), datatype=XSD.integer)))

        venue_name = e.get("journal") or e.get("booktitle")
        if venue_name:
            venue_name = norm_space(venue_name)
            venue = mint_venue_uri(args.base, venue_name)
            g.add((venue, RDF.type, SCHEMA.CreativeWork))
            add_lit(g, venue, SCHEMA.name, venue_name)
            g.add((work, SCHEMA.isPartOf, venue))

        pub_name = e.get("publisher")
        if pub_name:
            pub_name = norm_space(pub_name)
            org = mint_org_uri(args.base, pub_name)
            g.add((org, RDF.type, SCHEMA.Organization))
            add_lit(g, org, SCHEMA.name, pub_name)
            g.add((work, SCHEMA.publisher, org))

        for aname in split_people(e.get("author")):
            if aname.startswith("https://orcid.org/"):
                g.add((work, SCHEMA.author, URIRef(aname)))
                continue
            person = mint_person_uri(args.base, aname)
            g.add((person, RDF.type, SCHEMA.Person))
            add_lit(g, person, SCHEMA.name, aname)
            g.add((work, SCHEMA.author, person))

        for ename in split_people(e.get("editor")):
            if ename.startswith("https://orcid.org/"):
                g.add((work, SCHEMA.editor, URIRef(ename)))
                continue
            person = mint_person_uri(args.base, ename)
            g.add((person, RDF.type, SCHEMA.Person))
            add_lit(g, person, SCHEMA.name, ename)
            g.add((work, SCHEMA.editor, person))

        place = e.get("place")
        if place:
            add_lit(g, work, SCHEMA.location, norm_space(place))

        edition = e.get("edition")
        if edition:
            add_lit(g, work, SCHEMA.bookEdition, norm_space(edition))

        for extra in [e.get("series"), e.get("collection")]:
            if extra:
                extra_name = norm_space(extra)
                extra_node = mint_venue_uri(args.base, extra_name)
                g.add((extra_node, RDF.type, SCHEMA.CreativeWork))
                add_lit(g, extra_node, SCHEMA.name, extra_name)
                g.add((work, SCHEMA.isPartOf, extra_node))

    g.serialize(destination=args.output_nt, format="nt")
    print(f"Wrote {len(g)} triples to {args.output_nt}")
    print(f"Entries: {n_entries} | DOI ok: {n_doi_ok} | DOI raw kept: {n_doi_raw}")

if __name__ == "__main__":
    main()
