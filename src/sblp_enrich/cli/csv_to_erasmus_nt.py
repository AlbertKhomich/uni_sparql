#!/usr/bin/env python3

import csv
import re
import argparse
import unicodedata
from pathlib import Path
from urllib.parse import quote
from rapidfuzz import fuzz, process


BASE = "http://upbkg.data.dice-research.org"
SCHEMA = "https://schema.org/"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
RDFS_LABEL = "http://www.w3.org/2000/01/rdf-schema#label"
XSD_DATE = "http://www.w3.org/2001/XMLSchema#date"

PROJECT_CLASS = f"{SCHEMA}ResearchProject"
PROGRAM_CLASS = f"{SCHEMA}Program"

PROJECT_FUNDING_PROGRAM = f"{SCHEMA}funding"
PROJECT_PARTNER = f"{SCHEMA}participant"
SCHEMA_NAME = f"{SCHEMA}name"
SCHEMA_ALT_NAME = f"{SCHEMA}alternateName"
SCHEMA_START_DATE = f"{SCHEMA}startDate"
SCHEMA_END_DATE = f"{SCHEMA}endDate"


def nt_uri(uri: str) -> str:
    return f"<{uri}>"


def nt_lit(value: str, lang: str | None = None, datatype: str | None = None) -> str:
    value = str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").strip()

    if datatype:
        return f'"{value}"^^<{datatype}>'
    if lang:
        return f'"{value}"@{lang}'
    return f'"{value}"'


def triple(s: str, p: str, o: str) -> str:
    return f"{nt_uri(s)} {nt_uri(p)} {o} ."


def slugify(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = value.encode("ascii", "ignore").decode("ascii")
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = value.strip("-")
    return value or "unknown"


def normalize_text(value: str) -> str:
    value = value.lower()
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.replace("&", " and ")
    value = re.sub(r"[^\w\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def detect_dialect(path: Path):
    sample = path.read_text(encoding="utf-8-sig")[:4096]
    return csv.Sniffer().sniff(sample, delimiters=",;\t")


def read_csv(path: Path):
    dialect = detect_dialect(path)
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        yield from csv.DictReader(f, dialect=dialect)


def load_affiliations(path: Path):
    rows = list(read_csv(path))

    affiliations = []
    for row in rows:
        uri = row.get("affiliation", "").strip()
        name = row.get("affiliationName", "").strip()

        if not uri or not name:
            continue

        affiliations.append({
            "uri": uri,
            "name": name,
            "norm": normalize_text(name),
        })

    return affiliations


def extract_program_from_mittelherkunft(value: str):
    """
    Example:
    Erasmus + 2021-2027 Cooperation Partnership

    Output:
    {
        original_label: Erasmus + 2021-2027 Cooperation Partnership
        clean_name: Erasmus+ Cooperation Partnership
        start_year: 2021
        end_year: 2027
    }
    """
    value = (value or "").strip()
    if not value:
        return None

    if "erasmus" not in value.lower():
        return None

    year_match = re.search(r"\b(20\d{2})\s*[-–]\s*(20\d{2})\b", value)

    start_year = None
    end_year = None

    clean_name = value

    if year_match:
        start_year = year_match.group(1)
        end_year = year_match.group(2)
        clean_name = (
            value[:year_match.start()] + value[year_match.end():]
        ).strip()

    clean_name = clean_name.replace("Erasmus +", "Erasmus+")
    clean_name = re.sub(r"\s+", " ", clean_name).strip(" -–,")

    return {
        "original_label": value,
        "clean_name": clean_name,
        "start_year": start_year,
        "end_year": end_year,
    }


def program_uri(program_info: dict) -> str:
    """
    Makes stable URI from normalized Erasmus program name and years.
    """
    name_slug = slugify(program_info["clean_name"])

    if program_info["start_year"] and program_info["end_year"]:
        return f"{BASE}/funding-program/{name_slug}-{program_info['start_year']}-{program_info['end_year']}"

    return f"{BASE}/funding-program/{name_slug}"


def project_uri(row: dict) -> str:
    nr = row.get("Nr", "").strip()
    title = row.get("Projekttitel", "").strip()

    if nr:
        return f"{BASE}/project/{quote(nr)}"

    return f"{BASE}/project/{slugify(title)}"


def candidate_partner_windows(partner_text: str, max_window: int = 4):
    """
    Kooperationspartner is comma-separated, but some names contain commas.
    So we create candidate windows:

    chunks:
      ["National University of Science and Technology POLITEHNICA (Bukarest", "Rumänien)", ...]

    windows:
      chunk 1
      chunk 1 + chunk 2
      chunk 1 + chunk 2 + chunk 3

    This allows matching names that were broken by internal commas.
    """
    chunks = [c.strip() for c in partner_text.split(",") if c.strip()]
    candidates = []

    for i in range(len(chunks)):
        for size in range(1, max_window + 1):
            window = chunks[i:i + size]
            if not window:
                continue

            raw = ", ".join(window).strip()
            norm = normalize_text(raw)

            if len(norm) >= 4:
                candidates.append({
                    "raw": raw,
                    "norm": norm,
                    "start": i,
                    "end": i + size,
                })

    return candidates


def fuzzy_match_partners(partner_text: str, affiliations: list[dict], threshold: int = 80):
    if not partner_text:
        return []

    affiliation_norms = [a["norm"] for a in affiliations]
    candidates = candidate_partner_windows(partner_text)

    matches = []

    for cand in candidates:
        result = process.extractOne(
            cand["norm"],
            affiliation_norms,
            scorer=fuzz.ratio
        )

        if not result:
            continue

        matched_norm, score, idx = result
        if score >= threshold:
            affiliation = affiliations[idx]

            matches.append({
                "candidate": cand["raw"],
                "candidate_start": cand["start"],
                "candidate_end": cand["end"],
                "affiliation_uri": affiliation["uri"],
                "affiliation_name": affiliation["name"],
                "score": score,
            })

    # Greedy cleanup:
    # Prefer higher score and longer candidate windows, avoid overlapping fragments.
    matches.sort(
        key=lambda m: (
            m["score"],
            m["candidate_end"] - m["candidate_start"],
            len(m["candidate"])
        ),
        reverse=True
    )

    accepted = []
    occupied = set()
    seen_uris = set()

    for m in matches:
        positions = set(range(m["candidate_start"], m["candidate_end"]))

        if positions & occupied:
            continue

        if m["affiliation_uri"] in seen_uris:
            continue

        accepted.append(m)
        occupied |= positions
        seen_uris.add(m["affiliation_uri"])

    accepted.sort(key=lambda m: m["candidate_start"])
    return accepted


def write_nt(projects_csv: Path, affiliations_csv: Path, output_nt: Path, threshold: int):
    affiliations = load_affiliations(affiliations_csv)

    triples = []
    program_seen = set()

    for row in read_csv(projects_csv):
        program_info = extract_program_from_mittelherkunft(row.get("Mittelherkunft", ""))

        if not program_info:
            continue

        p_uri = project_uri(row)
        prog_uri = program_uri(program_info)

        title = row.get("Projekttitel", "").strip()
        abbreviation = row.get("Abkürzung", "").strip()
        project_begin = row.get("Projektbeginn", "").strip()
        project_end = row.get("Projektende", "").strip()

        triples.append(triple(p_uri, RDF_TYPE, nt_uri(PROJECT_CLASS)))

        if title:
            triples.append(triple(p_uri, SCHEMA_NAME, nt_lit(title, lang="de")))

        if abbreviation:
            triples.append(triple(p_uri, SCHEMA_ALT_NAME, nt_lit(abbreviation)))

        if project_begin:
            triples.append(triple(p_uri, SCHEMA_START_DATE, nt_lit(project_begin, datatype=XSD_DATE)))

        if project_end:
            triples.append(triple(p_uri, SCHEMA_END_DATE, nt_lit(project_end, datatype=XSD_DATE)))

        triples.append(triple(p_uri, PROJECT_FUNDING_PROGRAM, nt_uri(prog_uri)))

        if prog_uri not in program_seen:
            triples.append(triple(prog_uri, RDF_TYPE, nt_uri(PROGRAM_CLASS)))
            triples.append(triple(prog_uri, SCHEMA_NAME, nt_lit(program_info["clean_name"], lang="en")))
            triples.append(triple(prog_uri, RDFS_LABEL, nt_lit(program_info["original_label"], lang="en")))

            if program_info["start_year"]:
                triples.append(
                    triple(
                        prog_uri,
                        SCHEMA_START_DATE,
                        nt_lit(f"{program_info['start_year']}-01-01", datatype=XSD_DATE)
                    )
                )

            if program_info["end_year"]:
                triples.append(
                    triple(
                        prog_uri,
                        SCHEMA_END_DATE,
                        nt_lit(f"{program_info['end_year']}-12-31", datatype=XSD_DATE)
                    )
                )

            program_seen.add(prog_uri)

        partner_text = row.get("Kooperationspartner", "").strip()
        partner_matches = fuzzy_match_partners(
            partner_text=partner_text,
            affiliations=affiliations,
            threshold=threshold
        )

        for match in partner_matches:
            aff_uri = match["affiliation_uri"]

            triples.append(triple(p_uri, PROJECT_PARTNER, nt_uri(aff_uri)))

            # This is the direct "affiliation linked with Erasmus entity" triple.
            triples.append(triple(prog_uri, PROJECT_PARTNER, nt_uri(aff_uri)))

    output_nt.write_text("\n".join(triples) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--projects", required=True, help="Input project CSV")
    parser.add_argument("--affiliations", required=True, help="affiliation-name.csv")
    parser.add_argument("--out", required=True, help="Output .nt file")
    parser.add_argument("--threshold", type=int, default=100, help="Fuzzy matching threshold")
    args = parser.parse_args()

    write_nt(
        projects_csv=Path(args.projects),
        affiliations_csv=Path(args.affiliations),
        output_nt=Path(args.out),
        threshold=args.threshold
    )


if __name__ == "__main__":
    main()