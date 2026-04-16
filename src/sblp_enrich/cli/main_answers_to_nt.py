import argparse
import json
from typing import Dict, Optional, Set, Tuple
from urllib.parse import urlparse

from rdflib.term import _is_valid_uri


DEFAULT_PREDICATE = "https://schema.org/codeRepository"


def _is_http_uri(v: str) -> bool:
    s = (v or "").strip()
    if not s:
        return False
    if not _is_valid_uri(s):
        return False
    sl = s.lower()
    return sl.startswith("https://") or sl.startswith("http://")


def _to_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def canonicalize_github_repo_url(url: str) -> Optional[str]:
    s = (url or "").strip()
    if not s:
        return None
    if not s.lower().startswith(("https://", "http://")):
        return None

    try:
        p = urlparse(s)
    except Exception:
        return None

    host = (p.netloc or "").lower()
    if host not in {"github.com", "www.github.com"}:
        return None

    parts = [x for x in (p.path or "").split("/") if x]
    if len(parts) < 2:
        return None

    owner = parts[0].strip()
    repo = parts[1].strip()
    if repo.endswith(".git"):
        repo = repo[:-4]

    if not owner or not repo:
        return None

    canon = f"https://github.com/{owner}/{repo}"
    if not _is_http_uri(canon):
        return None
    return canon


def choose_pub_uri(row: Dict) -> Optional[str]:
    pub = (row.get("pub") or "").strip()
    if _is_http_uri(pub):
        return pub

    rj = row.get("response_json") or {}
    if isinstance(rj, dict):
        paper_uri = (rj.get("paper_uri") or "").strip()
        if _is_http_uri(paper_uri):
            return paper_uri
    return None


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Convert LLM answers JSONL to N-Triples paper->GitHub repository links."
    )
    ap.add_argument("--in-jsonl", required=True)
    ap.add_argument("--out-nt", required=True)
    ap.add_argument("--min-confidence", type=float, default=0.6)
    ap.add_argument("--predicate", default=DEFAULT_PREDICATE)
    ap.add_argument(
        "--status",
        default="ok",
        help="Comma-separated statuses to accept (default: ok)",
    )
    args = ap.parse_args()

    pred = (args.predicate or "").strip()
    if not _is_http_uri(pred):
        raise SystemExit(f"Invalid predicate URI: {pred}")

    accepted_status: Set[str] = {x.strip() for x in (args.status or "").split(",") if x.strip()}
    if not accepted_status:
        accepted_status = {"ok"}

    total_rows = 0
    rows_with_status = 0
    rows_with_pub = 0
    links_seen = 0
    links_kept = 0
    links_low_conf = 0
    links_invalid = 0

    triples: Set[Tuple[str, str, str]] = set()

    with open(args.in_jsonl, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            total_rows += 1
            try:
                row = json.loads(s)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue

            status = (row.get("status") or "").strip()
            if status not in accepted_status:
                continue
            rows_with_status += 1

            pub_uri = choose_pub_uri(row)
            if not pub_uri:
                continue
            rows_with_pub += 1

            rj = row.get("response_json") or {}
            if not isinstance(rj, dict):
                continue

            rel = rj.get("related_github") or []
            if not isinstance(rel, list):
                continue

            for item in rel:
                if not isinstance(item, dict):
                    continue
                links_seen += 1

                conf = _to_float(item.get("confidence"))
                if conf is None or conf < args.min_confidence:
                    links_low_conf += 1
                    continue

                u = canonicalize_github_repo_url(item.get("url") or "")
                if not u:
                    links_invalid += 1
                    continue

                triples.add((pub_uri, pred, u))
                links_kept += 1

    with open(args.out_nt, "w", encoding="utf-8") as out:
        for s, p, o in sorted(triples):
            out.write(f"<{s}> <{p}> <{o}> .\n")

    print(f"Input rows: {total_rows}")
    print(f"Rows matching status filter: {rows_with_status}")
    print(f"Rows with valid publication URI: {rows_with_pub}")
    print(f"Candidate links seen: {links_seen}")
    print(f"Links kept (confidence >= {args.min_confidence}): {links_kept}")
    print(f"Links dropped (low confidence): {links_low_conf}")
    print(f"Links dropped (invalid/non-repo URL): {links_invalid}")
    print(f"Unique triples written: {len(triples)}")
    print(f"Wrote: {args.out_nt}")


if __name__ == "__main__":
    main()
