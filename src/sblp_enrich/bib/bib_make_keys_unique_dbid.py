#!/usr/bin/env python3
"""
bib_make_keys_unique_dbid.py
Make duplicate BibTeX entry keys unique using nearby % DBID comments.
"""

from __future__ import annotations

import argparse
import shutil
from collections import Counter
from pathlib import Path
import re
from typing import Dict, List, Optional, Set

DBID_RE = re.compile(r"^\s*%\s*dbid\s*:\s*([0-9]+)\s*$", re.IGNORECASE)
ENTRY_HEAD_RE = re.compile(r"^(\s*@\w+\s*[{(]\s*)([^,\s]+)(\s*,.*)$")


def _make_unique_key(base_key: str, dbid: Optional[str], used: Set[str]) -> str:
    if dbid:
        candidate = f"{base_key}_{dbid}"
        if candidate not in used:
            return candidate
    i = 2
    while True:
        candidate = f"{base_key}_{i}"
        if candidate not in used:
            return candidate
        i += 1


def rewrite_text(text: str) -> tuple[str, Dict[str, int], List[tuple[str, str]]]:
    lines = text.splitlines(keepends=True)
    pending_dbid: Optional[str] = None
    entries: List[Dict[str, object]] = []

    for idx, line in enumerate(lines):
        m_dbid = DBID_RE.match(line)
        if m_dbid:
            pending_dbid = m_dbid.group(1)
            continue

        m_entry = ENTRY_HEAD_RE.match(line)
        if not m_entry:
            continue

        prefix, key, suffix = m_entry.groups()
        entries.append(
            {
                "line_idx": idx,
                "prefix": prefix,
                "key": key,
                "suffix": suffix,
                "dbid": pending_dbid,
            }
        )
        pending_dbid = None

    key_counts = Counter(str(e["key"]) for e in entries)
    used_keys: Set[str] = {k for k, c in key_counts.items() if c == 1}

    renamed = 0
    samples: List[tuple[str, str]] = []
    for e in entries:
        key = str(e["key"])
        if key_counts[key] == 1:
            used_keys.add(key)
            continue

        new_key = _make_unique_key(key, e.get("dbid"), used_keys)
        used_keys.add(new_key)
        if new_key == key:
            continue

        line_idx = int(e["line_idx"])
        prefix = str(e["prefix"])
        suffix = str(e["suffix"])
        lines[line_idx] = f"{prefix}{new_key}{suffix}"
        renamed += 1
        if len(samples) < 10:
            samples.append((key, new_key))

    stats = {
        "entries_total": len(entries),
        "duplicate_keys": sum(1 for k, c in key_counts.items() if c > 1),
        "keys_renamed": renamed,
    }
    return "".join(lines), stats, samples


def main() -> None:
    ap = argparse.ArgumentParser(description="Make duplicate BibTeX keys unique using % DBID suffixes.")
    ap.add_argument("bib_file", help="Path to .bib file")
    ap.add_argument("-o", "--output", help="Write to output file instead of editing in place")
    ap.add_argument("--dry-run", action="store_true", help="Do not write files, only report")
    ap.add_argument("--backup-suffix", default=".bak", help="Backup suffix for in-place edits (default: .bak)")
    args = ap.parse_args()

    in_path = Path(args.bib_file)
    text = in_path.read_text(encoding="utf-8", errors="replace")
    rewritten, stats, samples = rewrite_text(text)

    print(
        "Entries: {entries_total} | Duplicate keys: {duplicate_keys} | Keys renamed: {keys_renamed}".format(
            **stats
        )
    )
    for before, after in samples:
        print(f"- {before} -> {after}")

    if args.dry_run or stats["keys_renamed"] == 0:
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
