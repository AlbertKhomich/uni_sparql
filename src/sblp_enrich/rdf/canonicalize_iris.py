#!/usr/bin/env python3
"""Canonicalize external IRIs in N-Triples files to a DICE base IRI."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from contextlib import ExitStack
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TextIO
from urllib.parse import quote, urlsplit

SCHEMA_SAMEAS_IRI = "https://schema.org/sameAs"
LANGTAG_RE = re.compile(r"[A-Za-z0-9-]+")


class NTriplesParseError(ValueError):
    """Raised when an input line is not valid N-Triples syntax."""


@dataclass
class ParsedTriple:
    subject: str
    predicate: str
    object_token: str
    object_kind: str
    trailing_comment: str | None


@dataclass
class RewriteReportRow:
    external_iri: str
    canonical_iri: str
    source: str
    count_subject_rewrites: int
    count_object_rewrites: int
    first_seen_line: int


@dataclass
class RunStats:
    total_triples_processed: int = 0
    total_subject_rewrites: int = 0
    total_object_rewrites: int = 0
    sameas_triples_emitted: int = 0


@dataclass(frozen=True)
class Config:
    input_path: Path
    output_path: Path
    sameas_path: Path
    report_path: Path
    dry_run: bool
    base: str
    id_prefix: str


def normalize_base(base: str) -> str:
    value = base.strip()
    if not value:
        raise ValueError("Base IRI cannot be empty.")
    if value.startswith("<") and value.endswith(">") and len(value) >= 2:
        value = value[1:-1].strip()
    elif value.startswith("<") or value.endswith(">"):
        value = value.strip("<>").strip()
    if not value:
        raise ValueError("Base IRI cannot be empty after trimming angle brackets.")
    parsed = urlsplit(value)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("Base IRI must be absolute, e.g. https://dice-research.org")
    if parsed.query or parsed.fragment:
        raise ValueError("Base IRI must not contain query or fragment.")
    return value.rstrip("/")


def normalize_id_prefix(id_prefix: str) -> str:
    value = id_prefix.strip()
    if not value:
        return ""
    stripped = value.strip("/")
    if not stripped:
        return ""
    return "/" + stripped


def parse_iri_token(text: str, start: int) -> tuple[str, int] | None:
    if start >= len(text) or text[start] != "<":
        return None
    end = text.find(">", start + 1)
    if end == -1:
        return None
    if "<" in text[start + 1 : end]:
        return None
    return text[start : end + 1], end + 1


def parse_bnode_token(text: str, start: int) -> tuple[str, int] | None:
    if not text.startswith("_:", start):
        return None
    idx = start + 2
    if idx >= len(text):
        return None

    def is_label_char(ch: str) -> bool:
        return ch.isalnum() or ch in {"_", "-"}

    if not is_label_char(text[idx]):
        return None
    idx += 1

    while idx < len(text):
        ch = text[idx]
        if is_label_char(ch):
            idx += 1
            continue
        if ch == ".":
            nxt = text[idx + 1] if idx + 1 < len(text) else ""
            if nxt and is_label_char(nxt):
                idx += 1
                continue
        break
    return text[start:idx], idx


def parse_literal_token(text: str, start: int) -> tuple[str, int] | None:
    if start >= len(text) or text[start] != '"':
        return None
    idx = start + 1
    while idx < len(text):
        ch = text[idx]
        if ch == "\\":
            if idx + 1 >= len(text):
                return None
            idx += 2
            continue
        if ch == '"':
            break
        idx += 1
    if idx >= len(text) or text[idx] != '"':
        return None

    idx += 1

    if idx < len(text) and text[idx] == "@":
        lang_start = idx + 1
        match = LANGTAG_RE.match(text, lang_start)
        if match is None:
            return None
        idx = match.end()
    elif idx + 1 < len(text) and text[idx : idx + 2] == "^^":
        dtype = parse_iri_token(text, idx + 2)
        if dtype is None:
            return None
        _, idx = dtype

    return text[start:idx], idx


def parse_object_token(text: str, start: int) -> tuple[str, str, int] | None:
    iri = parse_iri_token(text, start)
    if iri is not None:
        token, end = iri
        return token, "iri", end
    bnode = parse_bnode_token(text, start)
    if bnode is not None:
        token, end = bnode
        return token, "bnode", end
    literal = parse_literal_token(text, start)
    if literal is not None:
        token, end = literal
        return token, "literal", end
    return None


def skip_ws(text: str, start: int) -> int:
    idx = start
    while idx < len(text) and text[idx].isspace():
        idx += 1
    return idx


def parse_nt_line(line: str, line_number: int) -> ParsedTriple | None:
    text = line.rstrip("\n")
    if not text.strip():
        return None

    idx = skip_ws(text, 0)
    if idx >= len(text) or text[idx] == "#":
        return None

    subject = parse_iri_token(text, idx) or parse_bnode_token(text, idx)
    if subject is None:
        raise NTriplesParseError(f"Line {line_number}: invalid subject.")
    subject_token, idx = subject

    idx = skip_ws(text, idx)
    predicate = parse_iri_token(text, idx)
    if predicate is None:
        raise NTriplesParseError(f"Line {line_number}: invalid predicate.")
    predicate_token, idx = predicate

    idx = skip_ws(text, idx)
    obj = parse_object_token(text, idx)
    if obj is None:
        raise NTriplesParseError(f"Line {line_number}: invalid object.")
    object_token, object_kind, idx = obj

    idx = skip_ws(text, idx)
    if idx >= len(text) or text[idx] != ".":
        raise NTriplesParseError(f"Line {line_number}: triple must end with '.'.")
    idx += 1
    idx = skip_ws(text, idx)

    trailing_comment: str | None = None
    if idx < len(text):
        if text[idx] != "#":
            raise NTriplesParseError(f"Line {line_number}: unexpected trailing content after '.'.")
        trailing_comment = text[idx:]

    return ParsedTriple(
        subject=subject_token,
        predicate=predicate_token,
        object_token=object_token,
        object_kind=object_kind,
        trailing_comment=trailing_comment,
    )


def is_internal_iri(iri: str, base: str) -> bool:
    parsed_iri = urlsplit(iri)
    parsed_base = urlsplit(base)

    if not parsed_iri.scheme or not parsed_iri.netloc:
        return False
    if parsed_iri.scheme.lower() != parsed_base.scheme.lower():
        return False
    if (parsed_iri.hostname or "").lower() != (parsed_base.hostname or "").lower():
        return False
    if parsed_iri.port != parsed_base.port:
        return False

    base_path = (parsed_base.path or "").rstrip("/")
    iri_path = parsed_iri.path or ""

    if not base_path:
        return True
    return iri_path == base_path or iri_path.startswith(base_path + "/")


def compute_source_and_id_part(iri: str) -> tuple[str, str]:
    parsed = urlsplit(iri)
    host = (parsed.hostname or "").lower()
    path_no_leading_slash = parsed.path[1:] if parsed.path.startswith("/") else parsed.path

    if host == "ror.org":
        return "ror", path_no_leading_slash

    if host in {"www.wikidata.org", "wikidata.org"} and parsed.path.startswith("/entity/"):
        segments = [segment for segment in parsed.path.split("/") if segment]
        id_part = segments[-1] if segments else ""
        return "wikidata", id_part

    if host == "orcid.org":
        return "orcid", path_no_leading_slash

    source = (host or parsed.netloc or parsed.scheme or "unknown").replace(".", "_")
    id_part = path_no_leading_slash
    if parsed.query:
        id_part = f"{id_part}?{parsed.query}" if id_part else f"?{parsed.query}"
    return source, id_part


def mint_canonical_iri(iri: str, base: str, id_prefix: str) -> tuple[str, str]:
    source, id_part = compute_source_and_id_part(iri)
    canonical = f"{base}{id_prefix}/{source}" if id_prefix else f"{base}/{source}"
    if id_part:
        canonical = f"{canonical}/{id_part}"

    fragment = urlsplit(iri).fragment
    if fragment:
        canonical = f"{canonical}/{quote(fragment, safe='')}"
    return canonical, source


def register_rewrite(
    *,
    external_iri: str,
    canonical_iri: str,
    source: str,
    line_number: int,
    is_subject: bool,
    report_rows: dict[str, RewriteReportRow],
    sameas_seen: set[tuple[str, str]],
    sameas_writer: TextIO | None,
    stats: RunStats,
) -> None:
    row = report_rows.get(external_iri)
    if row is None:
        row = RewriteReportRow(
            external_iri=external_iri,
            canonical_iri=canonical_iri,
            source=source,
            count_subject_rewrites=0,
            count_object_rewrites=0,
            first_seen_line=line_number,
        )
        report_rows[external_iri] = row
    else:
        if row.canonical_iri != canonical_iri or row.source != source:
            raise ValueError(
                "Inconsistent canonical mapping for external IRI "
                f"{external_iri!r}: {row.canonical_iri!r} vs {canonical_iri!r}."
            )

    if is_subject:
        row.count_subject_rewrites += 1
        stats.total_subject_rewrites += 1
    else:
        row.count_object_rewrites += 1
        stats.total_object_rewrites += 1

    sameas_key = (canonical_iri, external_iri)
    if sameas_key not in sameas_seen:
        sameas_seen.add(sameas_key)
        stats.sameas_triples_emitted += 1
        if sameas_writer is not None:
            sameas_writer.write(f"<{canonical_iri}> <{SCHEMA_SAMEAS_IRI}> <{external_iri}> .\n")


def rewrite_iri(iri: str, base: str, id_prefix: str) -> tuple[str, str] | None:
    if is_internal_iri(iri, base):
        return None
    canonical, source = mint_canonical_iri(iri, base, id_prefix)
    return canonical, source


def collect_external_subject_iris(input_path: Path, base: str) -> set[str]:
    """Collect external IRIs that occur as subjects and therefore may drive object rewrites."""
    subjects: set[str] = set()
    with input_path.open("r", encoding="utf-8") as in_f:
        for line_number, line in enumerate(in_f, start=1):
            parsed = parse_nt_line(line, line_number)
            if parsed is None:
                continue
            if not parsed.subject.startswith("<"):
                continue
            subject_iri = parsed.subject[1:-1]
            if not is_internal_iri(subject_iri, base):
                subjects.add(subject_iri)
    return subjects


def process_file(config: Config) -> tuple[RunStats, dict[str, RewriteReportRow]]:
    stats = RunStats()
    report_rows: dict[str, RewriteReportRow] = {}
    sameas_seen: set[tuple[str, str]] = set()
    rewritable_object_iris = collect_external_subject_iris(config.input_path, config.base)

    with ExitStack() as stack:
        in_f = stack.enter_context(config.input_path.open("r", encoding="utf-8"))
        out_f: TextIO | None = None
        sameas_f: TextIO | None = None

        if not config.dry_run:
            out_f = stack.enter_context(config.output_path.open("w", encoding="utf-8"))
            sameas_f = stack.enter_context(config.sameas_path.open("w", encoding="utf-8"))

        for line_number, line in enumerate(in_f, start=1):
            parsed = parse_nt_line(line, line_number)
            if parsed is None:
                if out_f is not None:
                    out_f.write(line)
                continue

            stats.total_triples_processed += 1

            subject_token = parsed.subject
            if subject_token.startswith("<"):
                subject_iri = subject_token[1:-1]
                subject_rewrite = rewrite_iri(subject_iri, config.base, config.id_prefix)
                if subject_rewrite is not None:
                    canonical, source = subject_rewrite
                    subject_token = f"<{canonical}>"
                    register_rewrite(
                        external_iri=subject_iri,
                        canonical_iri=canonical,
                        source=source,
                        line_number=line_number,
                        is_subject=True,
                        report_rows=report_rows,
                        sameas_seen=sameas_seen,
                        sameas_writer=sameas_f,
                        stats=stats,
                    )

            object_token = parsed.object_token
            if parsed.object_kind == "iri":
                object_iri = object_token[1:-1]
                if object_iri in rewritable_object_iris:
                    object_rewrite = rewrite_iri(object_iri, config.base, config.id_prefix)
                    if object_rewrite is not None:
                        canonical, source = object_rewrite
                        object_token = f"<{canonical}>"
                        register_rewrite(
                            external_iri=object_iri,
                            canonical_iri=canonical,
                            source=source,
                            line_number=line_number,
                            is_subject=False,
                            report_rows=report_rows,
                            sameas_seen=sameas_seen,
                            sameas_writer=sameas_f,
                            stats=stats,
                        )

            if out_f is not None:
                rewritten_line = f"{subject_token} {parsed.predicate} {object_token} ."
                if parsed.trailing_comment is not None:
                    rewritten_line += f" {parsed.trailing_comment}"
                out_f.write(rewritten_line + "\n")

    return stats, report_rows


def write_report(report_path: Path, rows: dict[str, RewriteReportRow]) -> None:
    ordered_rows = sorted(rows.values(), key=lambda row: row.first_seen_line)
    suffix = report_path.suffix.lower()

    if suffix == ".jsonl":
        with report_path.open("w", encoding="utf-8") as report_f:
            for row in ordered_rows:
                report_f.write(json.dumps(asdict(row), ensure_ascii=False) + "\n")
        return

    with report_path.open("w", encoding="utf-8", newline="") as report_f:
        writer = csv.DictWriter(
            report_f,
            fieldnames=[
                "external_iri",
                "canonical_iri",
                "source",
                "count_subject_rewrites",
                "count_object_rewrites",
                "first_seen_line",
            ],
        )
        writer.writeheader()
        for row in ordered_rows:
            writer.writerow(asdict(row))


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Canonicalize external subject/object IRIs in an N-Triples file to a DICE base IRI, "
            "emit schema:sameAs links, and write a rewrite report. "
            "Object IRIs are rewritten only if the same external IRI appears as a rewritten subject."
        )
    )
    parser.add_argument("--in", dest="input_path", required=True, help="Input N-Triples (.nt) file.")
    parser.add_argument("--out", required=True, help="Output file for rewritten triples.")
    parser.add_argument("--sameas", required=True, help="Output file for schema:sameAs triples.")
    parser.add_argument("--report", required=True, help="Rewrite report path (.csv or .jsonl).")
    parser.add_argument("--dry-run", action="store_true", help="Do not write --out/--sameas; still write --report.")
    parser.add_argument("--base", default="https://dice-research.org", help="Base IRI for canonical entities.")
    parser.add_argument("--id-prefix", default="/id", help="Canonical ID prefix path component.")
    return parser


def validate_paths(args: argparse.Namespace, parser: argparse.ArgumentParser) -> Config:
    input_path = Path(args.input_path)
    output_path = Path(args.out)
    sameas_path = Path(args.sameas)
    report_path = Path(args.report)

    if not input_path.exists():
        parser.error(f"Input file does not exist: {input_path}")
    if not input_path.is_file():
        parser.error(f"Input path is not a file: {input_path}")

    if not args.dry_run:
        in_resolved = input_path.resolve()
        if output_path.resolve() == in_resolved:
            parser.error("--out must be different from --in.")
        if sameas_path.resolve() == in_resolved:
            parser.error("--sameas must be different from --in.")

    try:
        base = normalize_base(args.base)
        id_prefix = normalize_id_prefix(args.id_prefix)
    except ValueError as exc:
        parser.error(str(exc))

    return Config(
        input_path=input_path,
        output_path=output_path,
        sameas_path=sameas_path,
        report_path=report_path,
        dry_run=bool(args.dry_run),
        base=base,
        id_prefix=id_prefix,
    )


def print_summary(stats: RunStats, unique_rewritten_iris: int) -> None:
    print(f"total triples processed: {stats.total_triples_processed}")
    print(f"total subject rewrites: {stats.total_subject_rewrites}")
    print(f"total object rewrites: {stats.total_object_rewrites}")
    print(f"number of unique external IRIs rewritten: {unique_rewritten_iris}")
    print(f"number of sameAs triples emitted: {stats.sameas_triples_emitted}")


def run(config: Config) -> int:
    try:
        stats, report_rows = process_file(config)
        write_report(config.report_path, report_rows)
    except (OSError, NTriplesParseError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print_summary(stats, unique_rewritten_iris=len(report_rows))
    if not config.dry_run:
        print(f"rewritten triples file: {config.output_path}")
        print(f"sameAs triples file: {config.sameas_path}")
    else:
        print("dry-run enabled: no rewritten/sameAs files were written")
    print(f"report file: {config.report_path}")
    return 0


def _run_selftest() -> int:
    import tempfile

    expected_fragment_canonical = (
        "https://dice-research.org/id/ror/058kzsd48/faculty-kulturwissenschaften"
    )
    minted, minted_source = mint_canonical_iri(
        "https://ror.org/058kzsd48#faculty-kulturwissenschaften",
        base="https://dice-research.org",
        id_prefix="/id",
    )
    assert minted == expected_fragment_canonical, "Fragment-to-path conversion failed."
    assert minted_source == "ror", "Source detection failed for ror."
    assert normalize_base("<https://dice-research.org>") == "https://dice-research.org"
    assert is_internal_iri("https://dice-research.org/id/org/0000e76a8102", "https://dice-research.org")

    sample_nt = (
        "<https://ror.org/058kzsd48#faculty-kulturwissenschaften> "
        "<https://example.org/p> "
        "<https://ror.org/058kzsd48#faculty-kulturwissenschaften> .\n"
        "<https://example.org/s2> <https://example.org/p> \"literal \\\"X\\\"\"@en .\n"
        "<https://dice-research.org/id/x/1> <https://example.org/type> <https://schema.org/Organization> .\n"
        "<https://ror.org/058kzsd48#faculty-kulturwissenschaften> <https://example.org/p> \"still literal\" .\n"
        "<https://dice-research.org/id/org/0000e76a8102> <https://example.org/p> <https://dice-research.org/id/org/0000e76a8102> .\n"
    )

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        input_path = root / "in.nt"
        output_path = root / "out.nt"
        sameas_path = root / "sameas.nt"
        report_path = root / "report.csv"
        input_path.write_text(sample_nt, encoding="utf-8")

        cfg = Config(
            input_path=input_path,
            output_path=output_path,
            sameas_path=sameas_path,
            report_path=report_path,
            dry_run=False,
            base="https://dice-research.org",
            id_prefix="/id",
        )
        stats, report_rows = process_file(cfg)
        write_report(report_path, report_rows)

        out_text = output_path.read_text(encoding="utf-8")
        assert (
            f"<{expected_fragment_canonical}> <https://example.org/p> <{expected_fragment_canonical}> ."
            in out_text
        ), "Subject/object IRI rewrite failed."
        assert "\"literal \\\"X\\\"\"@en" in out_text, "Literal object should remain unchanged."
        assert (
            "<https://dice-research.org/id/x/1> <https://example.org/type> <https://schema.org/Organization> ."
            in out_text
        ), "Object-only external IRI should remain unchanged."

        sameas_text = sameas_path.read_text(encoding="utf-8")
        ror_sameas_line = (
            f"<{expected_fragment_canonical}> <{SCHEMA_SAMEAS_IRI}> "
            "<https://ror.org/058kzsd48#faculty-kulturwissenschaften> ."
        )
        assert sameas_text.count(ror_sameas_line) == 1, "sameAs triple for repeated external IRI must be deduplicated."
        assert "0000e76a8102" not in sameas_text, "Internal base IRIs must not produce sameAs triples."
        assert "schema.org/Organization" not in sameas_text, "Object-only external IRIs must not produce sameAs."

        with report_path.open("r", encoding="utf-8", newline="") as report_f:
            rows = list(csv.DictReader(report_f))
        ror_rows = [row for row in rows if row["external_iri"] == "https://ror.org/058kzsd48#faculty-kulturwissenschaften"]
        assert len(ror_rows) == 1, "Report must contain one row per unique external IRI."
        assert int(ror_rows[0]["count_subject_rewrites"]) == 2, "Expected two subject rewrites for repeated subject."
        assert int(ror_rows[0]["count_object_rewrites"]) == 1, "Expected one object rewrite for repeated object."

        assert stats.total_subject_rewrites == 3, "Unexpected subject rewrite total in selftest."
        assert stats.total_object_rewrites == 1, "Unexpected object rewrite total in selftest."
        assert (
            "https://dice-research.org/id/org/0000e76a8102" not in report_rows
        ), "Internal base IRI must not appear in rewrite report."
        assert (
            "https://schema.org/Organization" not in report_rows
        ), "Object-only external IRI must not appear in rewrite report."

    print("SELFTEST passed")
    return 0


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()
    config = validate_paths(args, parser)
    return run(config)


if __name__ == "__main__":
    if os.environ.get("SELFTEST") == "1":
        raise SystemExit(_run_selftest())
    raise SystemExit(main())
