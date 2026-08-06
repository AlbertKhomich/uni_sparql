#!/usr/bin/env python3
import argparse
import os
import sqlite3
import sys
import time
from typing import Optional, Set, Tuple

RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
DEFAULT_LABEL_PREDICATE = "http://www.w3.org/2000/01/rdf-schema#label"
DEFAULT_TYPE_URI = "http://schema.org/Observation"

def parse_uri(token: str) -> Optional[str]:
    token = token.strip()
    if token.startswith("<") and token.endswith(">"):
        return token[1:-1]
    return None

def parse_nt_literal(token: str) -> Optional[str]:
    """
    Parses an N-Triples literal token and returns a normalized string key.

    Normalization:
      - drops language tags:  "x"@en  -> "x"
      - lowercases lexical form: "Hello" -> "hello"
      - keeps datatype:       "1"^^<dt> stays "1"^^<dt>

    Returns None if token isn't a literal.
    """
    def lowercase_lex(lex: str) -> str:
        # Lowercase only literal characters; keep N-Triples escapes intact.
        if len(lex) < 2 or lex[0] != '"' or lex[-1] != '"':
            return lex
        out = ['"']
        i = 1
        end = len(lex) - 1
        while i < end:
            c = lex[i]
            if c == "\\" and i + 1 < end:
                esc = lex[i + 1]
                out.append(c)
                out.append(esc)
                i += 2
                if esc == "u":
                    for _ in range(4):
                        if i < end:
                            out.append(lex[i])
                            i += 1
                elif esc == "U":
                    for _ in range(8):
                        if i < end:
                            out.append(lex[i])
                            i += 1
                continue
            out.append(c.lower())
            i += 1
        out.append('"')
        return "".join(out)

    t = token.strip()
    if not t.startswith('"'):
        return None

    i = 1
    escaped = False
    while i < len(t):
        c = t[i]
        if escaped:
            escaped = False
        else:
            if c == "\\":
                escaped = True
            elif c == '"':
                lex = t[: i + 1]          # include quotes
                rest = t[i + 1 :].strip() # @lang or ^^<datatype> or empty

                # DROP language tag, KEEP datatype
                if rest.startswith("@"):
                    rest = ""
                # (else: keep ^^<...> or empty)
                return lowercase_lex(lex) + rest
        i += 1
    return None


def parse_nt_line(line: str) -> Optional[Tuple[str, str, str]]:
    """
    Very lightweight N-Triples-ish parser for lines like:
      <s> <p> <o> .
      <s> <p> "literal"@en .
    Returns (s_uri, p_uri, o_token_raw) where o_token_raw is either URI (no brackets)
    or literal key from parse_nt_literal.
    """
    line = line.strip()
    if not line or line[0] == "#":
        return None
    if not line.endswith("."):
        return None
    line = line[:-1].strip()

    if not line.startswith("<"):
        return None
    s_end = line.find("> ")
    if s_end == -1:
        return None
    s = parse_uri(line[: s_end + 1])
    rest = line[s_end + 2 :].lstrip()

    if not rest.startswith("<"):
        return None
    p_end = rest.find("> ")
    if p_end == -1:
        return None
    p = parse_uri(rest[: p_end + 1])
    o_raw = rest[p_end + 2 :].strip()

    if not s or not p or not o_raw:
        return None

    if o_raw.startswith("<"):
        o = parse_uri(o_raw)
        if o is None:
            return None
        return (s, p, o)
    else:
        lit = parse_nt_literal(o_raw)
        if lit is None:
            return None
        return (s, p, lit)

def setup_db(db_path: str, table: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL;")
    cur.execute("PRAGMA synchronous=OFF;")
    cur.execute("PRAGMA temp_store=MEMORY;")
    cur.execute("PRAGMA cache_size=-200000;")  # ~200MB cache (negative => KB)

    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS {table} (
            uri   TEXT PRIMARY KEY,
            label TEXT,
            is_typed INTEGER NOT NULL DEFAULT 0
        );
    """)
    # Backward compatibility for DBs created before is_typed existed.
    cols = {r[1] for r in cur.execute(f"PRAGMA table_info({table})")}
    if "is_typed" not in cols:
        cur.execute(
            f"ALTER TABLE {table} ADD COLUMN is_typed INTEGER NOT NULL DEFAULT 0"
        )
        # Old versions only inserted rows on matching rdf:type, so existing rows
        # are treated as typed.
        cur.execute(f"UPDATE {table} SET is_typed = 1")

    cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_label ON {table}(label);")
    cur.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{table}_typed_label ON {table}(is_typed, label);"
    )
    conn.commit()
    return conn

def fmt_gb(nbytes: int) -> str:
    return f"{nbytes / (1024**3):.2f} GB"

def br(u: str) -> str:
    """Wrap a raw URI as an N-Triples-style token."""
    return f"<{u}>"

def ingest(
    nt_path: str,
    conn: sqlite3.Connection,
    table: str,
    type_uris: Set[str],
    label_predicate: str,
    commit_every: int = 200000,
    progress_every_s: int = 5,
) -> None:
    total_bytes = os.path.getsize(nt_path)
    read_bytes = 0

    cur = conn.cursor()
    n_relevant = 0
    t0 = time.time()
    last_report = t0

    with open(nt_path, "rb") as f:
        for raw in f:
            read_bytes += len(raw)

            # parse
            try:
                line = raw.decode("utf-8", "replace")
            except Exception:
                continue

            t = parse_nt_line(line)
            if not t:
                continue
            s, p, o = t

            # handle relevant triples
            if p == RDF_TYPE and o in type_uris:
                cur.execute(
                    f"""
                    INSERT INTO {table}(uri, label, is_typed)
                    VALUES (?, NULL, 1)
                    ON CONFLICT(uri) DO UPDATE SET is_typed = 1
                    """,
                    (s,),
                )
                n_relevant += 1

            elif p == label_predicate:
                cur.execute(
                    f"""
                    INSERT INTO {table}(uri, label, is_typed)
                    VALUES (?, ?, 0)
                    ON CONFLICT(uri) DO UPDATE SET label = COALESCE({table}.label, excluded.label)
                    """,
                    (s, o),
                )
                n_relevant += 1

            # commit batching
            if n_relevant > 0 and (n_relevant % commit_every == 0):
                conn.commit()

            # progress (time-based)
            now = time.time()
            if now - last_report >= progress_every_s:
                elapsed = now - t0
                pct = (read_bytes / total_bytes * 100.0) if total_bytes else 0.0
                mb_s = (read_bytes / (1024**2)) / elapsed if elapsed > 0 else 0.0
                remaining_bytes = max(total_bytes - read_bytes, 0)
                eta_s = (remaining_bytes / (mb_s * 1024**2)) if mb_s > 0 else 0.0

                print(
                    f"Progress: {pct:6.2f}% | {fmt_gb(read_bytes)} / {fmt_gb(total_bytes)}"
                    f" | {mb_s:7.1f} MB/s | ETA ~ {eta_s/60:,.1f} min"
                    f" | relevant {n_relevant:,}",
                    file=sys.stderr,
                )
                last_report = now

    conn.commit()
    total_s = time.time() - t0
    print(
        f"Ingest done: read {fmt_gb(read_bytes)} in {total_s/60:.1f} min | relevant {n_relevant:,}",
        file=sys.stderr,
    )

def emit_pairs(conn: sqlite3.Connection, table: str, out_path: str, mode: str = "canonical") -> None:
    """
    mode:
      - canonical: for each label group, pick smallest uri as canonical and pair it with others
      - allpairs: emit all unique unordered pairs in the group

    Output TSV uses <...> wrapped URIs:
      <uri1>\t<uri2>\t1.0
    """
    cur = conn.cursor()
    cur.execute(f"""
        SELECT label
        FROM {table}
        WHERE label IS NOT NULL AND is_typed = 1
        GROUP BY label
        HAVING COUNT(*) > 1
    """)
    dup_labels = [r[0] for r in cur.fetchall()]
    print(f"Duplicate labels found: {len(dup_labels):,}", file=sys.stderr)

    with open(out_path, "w", encoding="utf-8") as out:
        for label in dup_labels:
            cur.execute(
                f"SELECT uri FROM {table} WHERE label = ? AND is_typed = 1 ORDER BY uri",
                (label,),
            )
            uris = [r[0] for r in cur.fetchall()]
            if len(uris) < 2:
                continue

            if mode == "allpairs":
                for i in range(len(uris)):
                    for j in range(i + 1, len(uris)):
                        out.write(f"{br(uris[i])}\t{br(uris[j])}\t1.0\n")
            else:
                canon = uris[0]
                for u in uris[1:]:
                    out.write(f"{br(canon)}\t{br(u)}\t1.0\n")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="Path to .nt file (60GB ok)")
    ap.add_argument("--db", default="labels.sqlite", help="SQLite DB path")
    ap.add_argument("--out", required=True, help="Output TSV: <uri1>\\t<uri2>\\t1.0")

    ap.add_argument(
        "--type-uri",
        action="append",
        dest="type_uris",
        help=(
            "RDF type URI to match. Repeat this option to include multiple types, "
            f'e.g. --type-uri "http://schema.org/Observation" --type-uri "http://schema.org/Place". '
            f"If not provided, defaults to {DEFAULT_TYPE_URI}"
        ),
    )
    ap.add_argument(
        "--label-predicate",
        default=DEFAULT_LABEL_PREDICATE,
        help=f"Predicate URI used as label (default: {DEFAULT_LABEL_PREDICATE})",
    )
    ap.add_argument(
        "--table",
        default="entities",
        help="SQLite table name to use (lets you reuse same db for different types)",
    )

    ap.add_argument("--mode", choices=["canonical", "allpairs"], default="canonical")
    ap.add_argument("--commit-every", type=int, default=200000)
    ap.add_argument("--progress-every-s", type=int, default=5)

    args = ap.parse_args()
    type_uris = set(args.type_uris) if args.type_uris else {DEFAULT_TYPE_URI}

    conn = setup_db(args.db, args.table)
    ingest(
        args.input,
        conn,
        table=args.table,
        type_uris=type_uris,
        label_predicate=args.label_predicate,
        commit_every=args.commit_every,
        progress_every_s=args.progress_every_s,
    )
    emit_pairs(conn, table=args.table, out_path=args.out, mode=args.mode)
    conn.close()

if __name__ == "__main__":
    main()
