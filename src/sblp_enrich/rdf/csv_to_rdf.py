from __future__ import annotations

import argparse
import csv
import re
import hashlib
from datetime import datetime
from typing import Dict, List, Optional, Tuple

XSD = "http://www.w3.org/2001/XMLSchema#"
RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
SCHEMA = "https://schema.org/"
DCTERMS = "http://purl.org/dc/terms/"

CC_MAP = {
    "cc_0_1_0": "https://creativecommons.org/publicdomain/zero/1.0/",
    "cc_by_4_0": "https://creativecommons.org/licenses/by/4.0/",
    "cc_by_sa_4_0": "https://creativecommons.org/licenses/by-sa/4.0/",
    "cc_by_nd_4_0": "https://creativecommons.org/licenses/by-nd/4.0/",
    "cc_by_nc_4_0": "https://creativecommons.org/licenses/by-nc/4.0/",
    "cc_by_nc_sa_4_0": "https://creativecommons.org/licenses/by-nc-sa/4.0/",
    "cc_by_nc_nd_4_0": "https://creativecommons.org/licenses/by-nc-nd/4.0/",
    "cc_by_nc_sa_3_0": "https://creativecommons.org/licenses/by-nc-sa/3.0/",
    "cc_by_nc_nd_3_0": "https://creativecommons.org/licenses/by-nc-nd/3.0/",
    "cc_by_sa_3_0": "https://creativecommons.org/licenses/by-sa/3.0/",
    "cc_by_3_0": "https://creativecommons.org/licenses/by/3.0/",
}

AUTHOR_ENTRY_RE = re.compile(r"^\s*(?P<name>.+?)\s*(?:\((?P<meta>.+)\))?\s*$", re.UNICODE)

META_KV_RE = re.compile(r"\s*(?P<key>[A-Za-z0-9_\-]+)\s*:\s*(?P<val>.+?)\s*$", re.UNICODE)

IDENT_TOKEN_RE = re.compile(r"^\s*(?P<typ>[A-Za-z0-9_\-]+)\s*:\s*(?P<val>.+?)\s*$", re.UNICODE)

DOI_PREFIX_RE = re.compile(r"^(?:doi\s*:\s*)", re.IGNORECASE)

ORCID_RE = re.compile(r"(\d{4}-\d{4}-\d{4}-\d{3}[\dX])")

def normalize_orcid(raw: str) -> str:
    s = (raw or "").strip()
    s = s.replace("https://orcid.org/", "").replace("http://orcid.org/", "").strip()
    s = re.sub(r"\s+", "", s)
    m = ORCID_RE.search(s)
    return m.group(1) if m else ""

def normalize_doi(raw: str) -> str:
    d = (raw or "").strip()
    d = DOI_PREFIX_RE.sub("", d)
    d = d.replace("\\textunderscore", "_")
    d = d.replace("\\_", "_")
    d = d.replace(" ", "")
    return d

def safe_http_iri_or_lit(u: str) -> str:
    u = (u or "").strip()
    if not u.lower().startswith(("http://", "https://")):
        return lit(u)
    if ("\\" in u) or any(ch.isspace() for ch in u):
        return lit(u)
    if any(ch in u for ch in ['<', '>', '"', '{', '}', '|', '^', '`']):
        return lit(u)
    return iri(u)

def uri(base: str, path: str) -> str:
    if not base.endswith("/"):
        base = base + "/"
    return f"<{base}{path.lstrip('/')}>"

def iri(s: str) -> str:
    return f"<{s}>"

def nt_escape_literal(s: str) -> str:
    return (
        s.replace("\\", "\\\\")
         .replace('"', '\\"')
         .replace("\n", "\\n")
         .replace("\r", "\\r")
         .replace("\t", "\\t")
    )

def lit(s: str, lang: Optional[str] = None, datatype_iri: Optional[str] = None) -> str:
    s2 = nt_escape_literal(s)
    if lang:
        return f"\"{s2}\"@{lang}"
    if datatype_iri:
        return f"\"{s2}\"^^<{datatype_iri}>"
    return f"\"{s2}\""

def is_int(s: str) -> bool:
    try:
        int(s)
        return True
    except Exception:
        return False

def is_date_yyyy_mm_dd(s: str) -> bool:
    try:
        datetime.strptime(s, "%Y-%m-%d")
        return True
    except Exception:
        return False

def sha1_12(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:12]

def normalize_lang(code: str) -> str:
    code = code.strip()
    return code.lower()

def parse_semicolon_list(field: str) -> List[str]:
    return [p.strip() for p in field.split(";") if p.strip()]

def parse_identifiers(field: str) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    if not field:
        return out
    for tok in parse_semicolon_list(field):
        m = IDENT_TOKEN_RE.match(tok)
        if not m:
            out.append(("UNKNOWN", tok))
            continue
        out.append((m.group("typ").upper(), m.group("val").strip()))
    return out

def parse_author_like(field: str) -> List[Dict[str, object]]:
    res: List[Dict[str, object]] = []
    if not field:
        return res
    for part in parse_semicolon_list(field):
        m = AUTHOR_ENTRY_RE.match(part)
        if not m:
            continue
        name = (m.group("name") or "").strip()
        meta_raw = (m.group("meta") or "").strip()
        meta: Dict[str, str] = {}
        if meta_raw:
            chunks = [c.strip() for c in meta_raw.split(",") if c.strip()]
            for ch in chunks:
                kv = META_KV_RE.match(ch)
                if kv:
                    meta[kv.group("key")] = kv.group("val").strip()
                else:
                    meta[f"token_{len(meta) + 1}"] = ch
        res.append({"name": name, "meta": meta})
    return res

def person_uri(base: str, name: str, meta: Dict[str, str]) -> str:
    orcid = meta.get('orcid') or meta.get("ORCID")
    if orcid:
        oc = normalize_orcid(orcid)
        if oc:
            return safe_http_iri_or_lit(f"https://orcid.org/{oc}")

    uni_id = meta.get("UNI-ID") or meta.get("uni-id") or meta.get("uniId")
    if uni_id and str(uni_id).upper() != "NA":
        return uri(base, f"id/person/uni/{uni_id}")

    key = name + "|" + "|".join([f"{k}={v}" for k, v in sorted(meta.items())])
    return uri(base, f"id/person/hash/{sha1_12(key)}")

def org_uri(base: str, org_name: str) -> str:
    return uri(base, f"id/org/{sha1_12(org_name.strip().lower())}")

def periodical_uri(base: str, name: str) -> str:
    return uri(base, f"id/periodical/{sha1_12(name.strip().lower())}")

def publication_uri(base: str, ris_id: str) -> str:
    return uri(base, f"id/publication/ris/{ris_id}")

def guess_schema_type(publikationstyp: str, dokumententyp: str) -> str:
    pt = (publikationstyp or "").lower()
    dt = (dokumententyp or "").lower()

    if "journal" in pt or "zeitschrift" in pt:
        return iri(SCHEMA + "ScholarlyArticle")
    if "konferenz" in pt or "conference" in pt:
        return iri(SCHEMA + "ScholarlyArticle")
    if "buch" in pt or "book" in pt:
        return iri(SCHEMA + "Book")
    if "kapitel" in pt or "chapter" in pt:
        return iri(SCHEMA + "Chapter")
    if "bericht" in pt or "report" in pt or "tech" in pt:
        return iri(SCHEMA + "Report")
    if "dissertation" in pt or "thesis" in pt or "qualifikations" in pt:
        return iri(SCHEMA + "Thesis")
    if "patent" in pt:
        return iri(SCHEMA + "Patent")
    if "software" in pt or "dataset" in pt or "daten" in pt:
        return iri(SCHEMA + "CreativeWork")

    if "artikel" in dt or "article" in dt:
        return iri(SCHEMA + "ScholarlyArticle")
    return iri(SCHEMA + "CreativeWork")

class Writer:
    def __init__(self, out_fp):
        self.out = out_fp

    def triple(self, s: str, p: str, o: str):
        self.out.write(f"{s} {p} {o} .\n")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input_csv")
    ap.add_argument("output")
    ap.add_argument("--base", default="https://dice-research.org/", help="Base stable IRI for minted resources.")
    ap.add_argument("--raw-only", action="store_true", help="Only emit lossless raw csv triples, no semantic layer")
    ap.add_argument("--no-raw", action="store_true", help="Do not emit raw csv triples")
    ap.add_argument("--limit", type=int, default=0, help="Only process first N data rows (0 = all)")
    args = ap.parse_args()

    base = args.base
    if not base.startswith("http"):
        raise SystemExit("--base must be an http(s) IRI")

    with open(args.input_csv, "r", encoding="utf-8", newline="") as f_in, open(args.output, "w", encoding="utf-8", newline="") as f_out:
        reader = csv.DictReader(f_in, delimiter=";", quotechar='"')
        w = Writer(f_out)

        raw_pred = {col: uri(base, f"vocab/csv/{col}") for col in reader.fieldnames or []}

        for i, row in enumerate(reader, start=1):
            if args.limit and i > args.limit:
                break

            ris_id = (row.get("id") or "").strip()
            if not ris_id:
                continue

            pub = publication_uri(base, ris_id)

            if not args.no_raw:
                for col, val in row.items():
                    if val is None:
                        continue
                    val = val.strip()
                    if val == "":
                        continue

                    if col == "jahr" and is_int(val) and len(val) == 4:
                        obj = lit(val, datatype_iri=XSD + "gYear")
                    elif col in ("seitenanzahl", "seitenbereichvon", "seitenbereichbis") and is_int(val):
                        obj = lit(val, datatype_iri=XSD + "integer")
                    elif col in ("zugriffsbeschraenktbis",) and is_date_yyyy_mm_dd(val):
                        obj = lit(val, datatype_iri=XSD + "date")
                    else:
                        obj = lit(val)

                    w.triple(pub, raw_pred[col], obj)

            if args.raw_only:
                continue

            titel = (row.get("titel") or "").strip()
            untertitel = (row.get("untertitel") or "").strip()
            jahr = (row.get("jahr") or "").strip()
            sprachen = (row.get("sprachen") or "").strip()
            publikationstyp = (row.get("publikationstyp") or "").strip()
            dokumententyp = (row.get("dokumententyp") or "").strip()

            schema_type = guess_schema_type(publikationstyp, dokumententyp)
            w.triple(pub, iri(RDF + "type"), schema_type)

            w.triple(pub, iri(SCHEMA + "identifier"), lit(ris_id))

            lang_tag = None
            if sprachen:
                langs = parse_semicolon_list(sprachen)
                if langs:
                    lang_tag = normalize_lang(langs[0])

            if titel:
                w.triple(pub, iri(SCHEMA + "name"), lit(titel, lang=lang_tag))
            if untertitel:
                w.triple(pub, iri(SCHEMA + "alternateName"), lit(untertitel, lang=lang_tag))

            if jahr and is_int(jahr) and len(jahr) == 4:
                w.triple(pub, iri(SCHEMA + "datePublished"), lit(jahr, datatype_iri=XSD + "gYear"))

            if sprachen:
                for lc in parse_semicolon_list(sprachen):
                    w.triple(pub, iri(SCHEMA + "inLanguage"), lit(normalize_lang(lc)))

            zsf = (row.get("zusammenfassung") or "").strip()
            de_abs = (row.get("DEzusammenfassung") or "").strip()
            en_abs = (row.get("ENzusammenfassung") or "").strip()
            if zsf:
                w.triple(pub, iri(SCHEMA + "abstract"), lit(zsf, lang=lang_tag))
            if de_abs:
                w.triple(pub, iri(SCHEMA + "abstract"), lit(de_abs, lang="de"))
            if en_abs:
                w.triple(pub, iri(SCHEMA + "abstract"), lit(en_abs, lang="en"))

            kws = (row.get("keywords") or "").strip()
            if kws:
                for kw in parse_semicolon_list(kws):
                    w.triple(pub, iri(SCHEMA + "keywords"), lit(kw, lang=lang_tag))

            journal = (row.get("fachzeitschrift") or "").strip()
            periodical = None
            if journal:
                periodical = periodical_uri(base, journal)
                w.triple(periodical, iri(RDF + "type"), iri(SCHEMA + "Periodical"))
                w.triple(periodical, iri(SCHEMA + "name"), lit(journal))
                w.triple(pub, iri(SCHEMA + "isPartOf"), periodical)

            verlag = (row.get("verlag") or "").strip()
            verlagsort = (row.get("verlagsort") or "").strip()
            if verlag:
                org = org_uri(base, verlag)
                w.triple(org, iri(RDF + "type"), iri(SCHEMA + "Organization"))
                w.triple(org, iri(SCHEMA + "name"), lit(verlag))
                if verlagsort:
                    w.triple(org, iri(SCHEMA + "location"), lit(verlagsort))
                w.triple(pub, iri(SCHEMA + "publisher"), org)

            band = (row.get("band") or "").strip()
            heft = (row.get("heft") or "").strip()
            if band:
                w.triple(pub, iri(SCHEMA + "volumeNumber"), lit(band))
            if heft:
                w.triple(pub, iri(SCHEMA + "issueNumber"), lit(heft))

            pstart = (row.get("seitenbereichvon") or "").strip()
            pend = (row.get("seitenbereichbis") or "").strip()
            pcount = (row.get("seitenanzahl") or "").strip()
            if pstart:
                w.triple(pub, iri(SCHEMA + "pageStart"), lit(pstart))
            if pend:
                w.triple(pub, iri(SCHEMA + "pageEnd"), lit(pend))
            if pcount and is_int(pcount):
                w.triple(pub, iri(SCHEMA + "numberOfPages"), lit(pcount, datatype_iri=XSD + "integer"))

            peer = (row.get("peerreviewed") or "").strip().lower()
            if peer in ("ja", "yes", "true"):
                w.triple(pub, uri(base, "vocab/peerReviewed"), lit("true", datatype_iri=XSD + "boolean"))
            elif peer in ("nein", "no", "false"):
                w.triple(pub, uri(base, "vocab/peerReviewed"), lit("false", datatype_iri=XSD + "boolean"))

            access = (row.get("zugangsrecht") or "").strip()
            if access:
                w.triple(pub, iri(DCTERMS + "accessRights"), lit(access))

            embargo = (row.get("zugriffsbeschraenktbis") or "").strip()
            if embargo and is_date_yyyy_mm_dd(embargo):
                w.triple(pub, uri(base, "vocab/embargoUntil"), lit(embargo, datatype_iri=XSD + "date"))

            lic = (row.get("lizenz") or "").strip()
            if lic:
                cc_url = CC_MAP.get(lic.lower())
                if cc_url:
                    w.triple(pub, iri(DCTERMS + "license"), iri(cc_url))
                else:
                    w.triple(pub, iri(DCTERMS + "license"), lit(lic))

            if publikationstyp:
                w.triple(pub, uri(base, "vocab/publicationType"), lit(publikationstyp))
            if dokumententyp:
                w.triple(pub, uri(base, "vocab/documentType"), lit(dokumententyp))

            ident_field = (row.get("identifier") or "").strip()
            idents = parse_identifiers(ident_field)
            for typ, val in idents:
                w.triple(pub, iri(SCHEMA + "identifier"), lit(f"{typ}:{val}"))
                if typ == "DOI":
                    doi = normalize_doi(val)
                    doi_iri = f"https://doi.org/{doi}"
                    w.triple(pub, iri(SCHEMA + "sameAs"), safe_http_iri_or_lit(doi_iri))
                elif typ == "URL":
                    w.triple(pub, iri(SCHEMA + "url"), safe_http_iri_or_lit(val))
                elif typ == "ISSN" and periodical:
                    w.triple(periodical, iri(SCHEMA + "issn"), lit(val.strip()))
                elif typ == "ISBN":
                    w.triple(pub, iri(SCHEMA + "isbn"), lit(val.strip()))

            authors_field = (row.get("autoren") or "").strip()
            authors = parse_author_like(authors_field)
            for a in authors:
                name = str(a.get("name") or "").strip()
                meta = a.get("meta") or {}
                assert isinstance(meta, dict)
                person = person_uri(base, name, meta)
                w.triple(person, iri(RDF + "type"), iri(SCHEMA + "Person"))
                if name:
                    w.triple(person, iri(SCHEMA + "name"), lit(name))
                uni_id = meta.get("UNI-ID")
                if uni_id and str(uni_id).upper() != "NA":
                    w.triple(person, uri(base, "vocab/uniId"), lit(str(uni_id)))
                orcid = meta.get("orcid") or meta.get("ORCID")
                if orcid:
                    oc = normalize_orcid(orcid)
                    if oc:
                        w.triple(person, iri(SCHEMA + "sameAs"), safe_http_iri_or_lit(f"https://orcid.org/{oc}"))

                for k, v in meta.items():
                    if k in ("UNI-ID", "orcid", "ORCID"):
                        continue
                    w.triple(person, uri(base, f"vocab/personMeta/{k}"), lit(str(v)))
                w.triple(pub, iri(SCHEMA + "author"), person)

            editors_field = (row.get("herausgeber") or "").strip()
            editors = parse_author_like(editors_field)
            for e in editors:
                name = str(e.get("name") or "").strip()
                meta = e.get("meta") or {}
                assert isinstance(meta, dict)
                person = person_uri(base, name, meta)
                w.triple(person,  iri(RDF + "type"), iri(SCHEMA + "Person"))
                if name:
                    w.triple(person, iri(SCHEMA + "name"), lit(name))
                uni_id = meta.get("UNI-ID")
                if uni_id and str(uni_id).upper() != "NA":
                    w.triple(person, uri(base, "vocab/uniId"), lit(str(uni_id)))
                orcid = meta.get("orcid") or meta.get("ORCID")
                if orcid:
                    oc = normalize_orcid(orcid)
                    if oc:
                        w.triple(person, iri(SCHEMA + "sameAs"), safe_http_iri_or_lit(f"https://orcid.org/{oc}"))
                for k, v in meta.items():
                    if k in ("UNI-ID", "orcid", "ORCID"):
                        continue
                    w.triple(person, uri(base, f"vocab/personMeta/{k}"), lit(str(v)))
                w.triple(pub, iri(SCHEMA + "editor"), person)


if __name__ == "__main__":
    main()
                    