import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from typing import Dict, List, Optional, Sequence, Tuple

import requests

import fuseki_schemaorg as fuseki


SCHEMA = "https://schema.org/"
PAPER_PREFIX = "https://dice-research.org/id/publication/"
URL_RE = re.compile(r"^<([^>]+)>\s+<https://schema\.org/url>\s+<([^>]+)>\s+\.\s*$")
GITHUB_RE = re.compile(
    r"(?i)\b(?:https?://|www\.)?github\.com/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)?(?:/[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]*)?"
)
PDFISH_RE = re.compile(r"(?i)(?:\.pdf(?:$|[?#])|/pdf(?:$|[/?#])|/article-pdf/|/content/pdf/)")


def normalize_space(s: str) -> str:
    return " ".join((s or "").split())


def is_http_url(s: str) -> bool:
    v = (s or "").strip().lower()
    return v.startswith("http://") or v.startswith("https://")


def parse_pdf_nt(path: str, limit: int = 0, pub_filter: Optional[set] = None) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    by_pub: Dict[str, List[str]] = {}

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            m = URL_RE.match(line.strip())
            if not m:
                continue

            pub = m.group(1).strip()
            pdf_url = m.group(2).strip()
            if PAPER_PREFIX not in pub:
                continue
            if pub_filter and pub not in pub_filter:
                continue
            if not is_http_url(pdf_url):
                continue

            by_pub.setdefault(pub, []).append(pdf_url)

    for pub, urls in by_pub.items():
        chosen = ""
        for u in urls:
            if PDFISH_RE.search(u):
                chosen = u
                break
        if not chosen:
            chosen = urls[0]

        out.append({"pub": pub, "pdf_url": chosen, "title": "", "abstract": ""})
        if limit and len(out) >= limit:
            break

    return out


def fetch_papers_with_pdf_url(endpoint_query: str, limit: int = 0, offset: int = 0) -> List[Dict[str, str]]:
    limit_clause = f"limit {int(limit)}" if int(limit) > 0 else ""
    offset_clause = f"offset {int(offset)}" if int(offset) > 0 else ""

    q = f"""
    prefix schema: <{SCHEMA}>

    select
      ?pub
      (sample(?title0) as ?title)
      (sample(?abs0) as ?abstract)
      (sample(?url0) as ?pdf_url)
    where {{
      ?pub schema:name ?title0 .
      ?pub schema:url ?url0 .

      filter(strstarts(str(?pub), "{PAPER_PREFIX}"))
      filter(isIRI(?url0))

      optional {{ ?pub schema:abstract ?abs0 . }}
    }}
    group by ?pub
    order by ?pub
    {limit_clause}
    {offset_clause}
    """.strip()

    rows = fuseki.sparql_select(endpoint_query, q)
    out = []
    for b in rows:
        out.append(
            {
                "pub": b["pub"]["value"],
                "title": b.get("title", {}).get("value", ""),
                "abstract": b.get("abstract", {}).get("value", ""),
                "pdf_url": b.get("pdf_url", {}).get("value", ""),
            }
        )
    return out


def fetch_metadata_for_pub_uris(endpoint_query: str, pub_uris: Sequence[str], batch_size: int = 200) -> Dict[str, Dict[str, str]]:
    out: Dict[str, Dict[str, str]] = {}
    n = len(pub_uris)

    for i in range(0, n, batch_size):
        batch = [u for u in pub_uris[i : i + batch_size] if u]
        if not batch:
            continue

        values = " ".join(f"<{u}>" for u in batch)
        q = f"""
        prefix schema: <{SCHEMA}>

        select
          ?pub
          (sample(?title0) as ?title)
          (sample(?abs0) as ?abstract)
        where {{
          values ?pub {{ {values} }}
          optional {{ ?pub schema:name ?title0 . }}
          optional {{ ?pub schema:abstract ?abs0 . }}
        }}
        group by ?pub
        """.strip()

        rows = fuseki.sparql_select(endpoint_query, q)
        for b in rows:
            pub = b["pub"]["value"]
            out[pub] = {
                "title": b.get("title", {}).get("value", ""),
                "abstract": b.get("abstract", {}).get("value", ""),
            }

    return out


def _pdf_cache_path(cache_dir: str, pub_uri: str, pdf_url: str) -> str:
    key = f"{pub_uri}|{pdf_url}".encode("utf-8", errors="ignore")
    h = hashlib.sha1(key).hexdigest()
    return os.path.join(cache_dir, f"{h}.pdf")


def _looks_like_pdf_header(prefix: bytes) -> bool:
    return b"%PDF" in (prefix or b"")[:1024]


def download_pdf(pdf_url: str, out_path: str, timeout_s: int, max_pdf_mb: int) -> Tuple[bool, str]:
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    headers = {
        "User-Agent": "uni-sparql-github-link-pipeline/1.0",
        "Accept": "application/pdf,*/*;q=0.8",
    }

    max_bytes = int(max_pdf_mb) * 1024 * 1024
    tmp_path = out_path + ".tmp"

    try:
        with requests.get(pdf_url, stream=True, timeout=(10, timeout_s), headers=headers, allow_redirects=True) as r:
            if r.status_code != 200:
                return False, f"http_{r.status_code}"

            first = b""
            total = 0

            with open(tmp_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    if len(first) < 2048:
                        first += chunk[: 2048 - len(first)]
                    total += len(chunk)
                    if total > max_bytes:
                        try:
                            os.remove(tmp_path)
                        except OSError:
                            pass
                        return False, "too_large"
                    f.write(chunk)

            ctype = (r.headers.get("Content-Type") or "").lower()
            if not _looks_like_pdf_header(first) and "pdf" not in ctype:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
                return False, "not_pdf"

        os.replace(tmp_path, out_path)
        return True, "downloaded"

    except requests.RequestException as e:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        return False, f"request_error:{type(e).__name__}"


def extract_text_with_mutool(pdf_path: str, max_pages: int, timeout_s: int, max_chars: int) -> Tuple[str, str]:
    if not shutil.which("mutool"):
        return "", "mutool_missing"

    cmd = ["mutool", "draw", "-q", "-F", "txt", "-i", "-o", "-", pdf_path]
    if max_pages > 0:
        cmd.append(f"1-{int(max_pages)}")

    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return "", "mutool_timeout"

    if proc.returncode != 0:
        return "", "mutool_failed"

    txt = proc.stdout or ""
    if max_chars > 0 and len(txt) > max_chars:
        txt = txt[:max_chars]

    return txt, "mutool"


def extract_text_with_tesseract_ocr(pdf_path: str, max_pages: int, timeout_s: int, max_chars: int) -> Tuple[str, str]:
    if not shutil.which("pdftoppm"):
        return "", "pdftoppm_missing"
    if not shutil.which("tesseract"):
        return "", "tesseract_missing"

    with tempfile.TemporaryDirectory(prefix="pdfocr_") as tmpdir:
        prefix = os.path.join(tmpdir, "page")
        cmd = ["pdftoppm", "-q", "-png"]
        if max_pages > 0:
            cmd.extend(["-f", "1", "-l", str(int(max_pages))])
        cmd.extend([pdf_path, prefix])

        try:
            render = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return "", "ocr_render_timeout"

        if render.returncode != 0:
            return "", "ocr_render_failed"

        image_paths = sorted(
            os.path.join(tmpdir, p) for p in os.listdir(tmpdir) if p.startswith("page-") and p.endswith(".png")
        )
        if not image_paths:
            return "", "ocr_no_images"

        chunks: List[str] = []
        for img in image_paths:
            try:
                ocr = subprocess.run(
                    ["tesseract", img, "stdout", "-l", "eng"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout_s,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                continue

            if ocr.returncode == 0 and ocr.stdout:
                chunks.append(ocr.stdout)

        txt = "\n".join(chunks)
        if max_chars > 0 and len(txt) > max_chars:
            txt = txt[:max_chars]
        if not txt.strip():
            return "", "ocr_empty"

        return txt, "ocr_tesseract"


def extract_text_from_raw_pdf_bytes(pdf_path: str, max_chars: int = 1_500_000) -> str:
    try:
        with open(pdf_path, "rb") as f:
            data = f.read(max_chars)
    except OSError:
        return ""

    txt = data.decode("latin-1", errors="ignore")
    txt = txt.replace("\x00", " ")
    return txt


def normalize_github_url(raw_url: str) -> str:
    u = (raw_url or "").strip()
    if not u:
        return ""

    while u and u[-1] in ".,;:)]}>\"'":
        u = u[:-1]
    if not u:
        return ""

    ul = u.lower()
    if ul.startswith("www.github.com/"):
        u = "https://" + u
    elif ul.startswith("github.com/"):
        u = "https://" + u

    return u


def find_github_mentions(text: str, window: int, source: str, limit: int = 50) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    seen = set()

    for m in GITHUB_RE.finditer(text or ""):
        raw = m.group(0)
        url = normalize_github_url(raw)
        if not url:
            continue

        left = max(0, m.start() - window)
        right = min(len(text), m.end() + window)
        ctx = normalize_space(text[left:right])

        key = (url, ctx)
        if key in seen:
            continue
        seen.add(key)

        out.append({"url": url, "context": ctx, "source": source})
        if len(out) >= limit:
            break

    return out


def build_prompt(pub: Dict[str, object], context_window: int) -> str:
    mentions: List[Dict[str, str]] = pub.get("github_mentions") or []

    if mentions:
        lines = []
        for i, m in enumerate(mentions, start=1):
            lines.append(f"{i}. URL: {m.get('url', '')}\\n   Context: {m.get('context', '')}")
        mentions_block = "\n".join(lines)
    else:
        mentions_block = "No explicit GitHub mention detected from extracted PDF text."

    prompt = (
        "You are linking a scholarly paper to its related GitHub repository, if one exists.\n\n"
        f"Paper URI: {pub.get('pub', '')}\n"
        f"Title: {pub.get('title', '')}\n"
        f"Abstract: {pub.get('abstract', '')}\n"
        f"PDF URL: {pub.get('pdf_url', '')}\n\n"
        f"Detected GitHub mentions in paper text (context window: +/-{context_window} chars):\n"
        f"{mentions_block}\n\n"
        "Task:\n"
        "1) Decide which GitHub repository URL(s) are truly related to THIS paper (code, data, model, benchmark, replication artifact).\n"
        "2) Ignore unrelated references (author profile, organization homepage, random citation, third-party dependency).\n"
        "3) If no related repository exists, return an empty list.\n\n"
        "Return strict JSON with this schema:\n"
        "{\n"
        "  \"paper_uri\": \"...\",\n"
        "  \"related_github\": [\n"
        "    {\n"
        "      \"url\": \"https://github.com/org/repo\",\n"
        "      \"confidence\": 0.0,\n"
        "      \"reason\": \"short reason\"\n"
        "    }\n"
        "  ],\n"
        "  \"evidence\": [\"short quoted snippets from context\"],\n"
        "  \"notes\": \"optional\"\n"
        "}"
    )
    return prompt


def iter_source_rows(args: argparse.Namespace) -> List[Dict[str, str]]:
    pub_filter = set(args.pub_uri or []) if args.pub_uri else None

    if args.pdf_nt:
        rows = parse_pdf_nt(args.pdf_nt, limit=args.limit, pub_filter=pub_filter)
        if args.fuseki_query and rows:
            meta = fetch_metadata_for_pub_uris(args.fuseki_query, [r["pub"] for r in rows])
            for r in rows:
                m = meta.get(r["pub"], {})
                if m:
                    r["title"] = m.get("title", "")
                    r["abstract"] = m.get("abstract", "")
        return rows

    all_rows: List[Dict[str, str]] = []
    offset = 0

    while True:
        batch_lim = args.batch_size
        if args.limit and args.limit > 0:
            remaining = args.limit - len(all_rows)
            if remaining <= 0:
                break
            batch_lim = min(batch_lim, remaining)

        batch = fetch_papers_with_pdf_url(args.fuseki_query, limit=batch_lim, offset=offset)
        if not batch:
            break

        for row in batch:
            pub = row.get("pub") or ""
            if pub_filter and pub not in pub_filter:
                continue
            all_rows.append(row)
            if args.limit and args.limit > 0 and len(all_rows) >= args.limit:
                break

        offset += len(batch)
        if args.limit and args.limit > 0 and len(all_rows) >= args.limit:
            break

    return all_rows


def main() -> None:
    ap = argparse.ArgumentParser(
        description=(
            "Build LLM prompts for paper->GitHub linking using schema:url PDF links, "
            "paper title/abstract, and GitHub mentions extracted from PDF text."
        )
    )
    ap.add_argument("--fuseki-query", required=False, help="SPARQL query endpoint URL")
    ap.add_argument("--pdf-nt", required=False, help="NT file with schema:url triples to reuse")
    ap.add_argument("--out-jsonl", required=True, help="Output JSONL with prompts and evidence")
    ap.add_argument("--out-prompts", required=False, help="Optional plain text output with concatenated prompts")
    ap.add_argument("--pdf-cache-dir", default=".pdf_cache", help="Directory to cache downloaded PDFs")
    ap.add_argument("--batch-size", type=int, default=300)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--pub-uri", action="append", help="Restrict to one/more publication URI(s)")
    ap.add_argument("--context-window", type=int, default=30)
    ap.add_argument("--max-pages", type=int, default=0, help="0 means all pages")
    ap.add_argument("--max-chars", type=int, default=1_000_000)
    ap.add_argument("--timeout", type=int, default=60)
    ap.add_argument("--max-pdf-mb", type=int, default=50)
    ap.add_argument("--no-ocr", action="store_true", help="Disable OCR fallback (pdftoppm + tesseract)")
    ap.add_argument("--skip-download", action="store_true")
    ap.add_argument(
        "--include-without-github",
        action="store_true",
        help="Include papers with zero detected GitHub mentions (default is to skip them)",
    )
    args = ap.parse_args()

    if not args.fuseki_query and not args.pdf_nt:
        raise SystemExit("Provide at least one source: --fuseki-query or --pdf-nt")

    rows = iter_source_rows(args)

    os.makedirs(os.path.dirname(args.out_jsonl) or ".", exist_ok=True)
    os.makedirs(args.pdf_cache_dir, exist_ok=True)

    processed = 0
    with_github = 0
    downloaded = 0
    cached = 0
    skipped = 0
    download_failed = 0

    prompts_fp = None
    if args.out_prompts:
        os.makedirs(os.path.dirname(args.out_prompts) or ".", exist_ok=True)
        prompts_fp = open(args.out_prompts, "w", encoding="utf-8")

    with open(args.out_jsonl, "w", encoding="utf-8") as out:
        for row in rows:
            pub_uri = row.get("pub", "")
            pdf_url = row.get("pdf_url", "").strip()
            title = row.get("title", "")
            abstract = row.get("abstract", "")

            rec: Dict[str, object] = {
                "pub": pub_uri,
                "pdf_url": pdf_url,
                "title": title,
                "abstract": abstract,
                "download_status": "",
                "pdf_cache_path": "",
                "text_sources": [],
                "github_mentions": [],
                "llm_prompt": "",
            }

            if not is_http_url(pdf_url):
                rec["download_status"] = "missing_or_invalid_url"
                skipped += 1
            else:
                pdf_path = _pdf_cache_path(args.pdf_cache_dir, pub_uri, pdf_url)
                rec["pdf_cache_path"] = pdf_path

                if os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 0:
                    ok = True
                    status = "cached"
                    cached += 1
                elif args.skip_download:
                    ok = False
                    status = "skip_download"
                else:
                    ok, status = download_pdf(pdf_url, pdf_path, timeout_s=args.timeout, max_pdf_mb=args.max_pdf_mb)
                    if ok:
                        downloaded += 1

                rec["download_status"] = status
                if status.startswith("request_error:") or status.startswith("http_") or status in {"not_pdf", "too_large"}:
                    download_failed += 1

                text = ""
                if ok:
                    text, method = extract_text_with_mutool(
                        pdf_path,
                        max_pages=args.max_pages,
                        timeout_s=args.timeout,
                        max_chars=args.max_chars,
                    )
                    if method:
                        rec["text_sources"].append(method)

                    mentions = find_github_mentions(text, window=args.context_window, source="pdf_text")

                    if not mentions and not args.no_ocr:
                        ocr_text, ocr_method = extract_text_with_tesseract_ocr(
                            pdf_path,
                            max_pages=args.max_pages,
                            timeout_s=args.timeout,
                            max_chars=args.max_chars,
                        )
                        if ocr_method:
                            rec["text_sources"].append(ocr_method)
                        if ocr_text:
                            mentions.extend(
                                find_github_mentions(
                                    ocr_text, window=args.context_window, source="pdf_ocr_text"
                                )
                            )

                    raw_txt = extract_text_from_raw_pdf_bytes(pdf_path, max_chars=args.max_chars)
                    raw_mentions = find_github_mentions(raw_txt, window=args.context_window, source="pdf_raw_bytes")

                    all_mentions = mentions + raw_mentions
                    dedup = []
                    seen = set()
                    for m in all_mentions:
                        k = (m["url"], m["context"])
                        if k in seen:
                            continue
                        seen.add(k)
                        dedup.append(m)

                    rec["github_mentions"] = dedup

            if rec["github_mentions"]:
                with_github += 1

            if not rec["github_mentions"] and not args.include_without_github:
                continue

            rec["llm_prompt"] = build_prompt(rec, context_window=args.context_window)
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")

            if prompts_fp is not None:
                prompts_fp.write(f"### {pub_uri}\n")
                prompts_fp.write(rec["llm_prompt"] + "\n\n")

            processed += 1

    if prompts_fp is not None:
        prompts_fp.close()

    print(f"Source rows: {len(rows)}")
    print(f"Written records: {processed}")
    print(f"Records with GitHub mentions: {with_github}")
    print(f"PDF downloaded now: {downloaded}")
    print(f"PDF reused from cache: {cached}")
    print(f"PDF download/extract failed before text extraction: {download_failed}")
    print(f"Rows skipped before PDF handling: {skipped}")
    print(f"Wrote JSONL: {args.out_jsonl}")
    if args.out_prompts:
        print(f"Wrote prompts: {args.out_prompts}")


if __name__ == "__main__":
    main()
