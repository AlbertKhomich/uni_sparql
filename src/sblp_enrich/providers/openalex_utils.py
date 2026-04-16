import re
from typing import Any, Dict, Optional

_DOI_RE = re.compile(r"10\.[0-9]{4,9}/\S+", re.IGNORECASE)

def normalize_doi_iri(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    s = (s or "").strip()
    if not s:
        return None

    sl = s.lower()
    if sl.startswith("doi:"):
        s = s.split(":", 1)[1].strip()
        return "https://doi.org/" + s

    if sl.startswith("https://doi.org/"):
        return "https://doi.org/" + s.split("doi.org/", 1)[1].lstrip()
    if sl.startswith("http://doi.org/"):
        return "https://doi.org/" + s.split("doi.org/", 1)[1].lstrip()

    m = _DOI_RE.search(s)
    if m:
        doi = m.group(0).rstrip(". ")
        return "https://doi.org/" + doi

    return None

def doi_from_work(work: Dict[str, Any]) -> Optional[str]:
    doi = work.get("doi")
    if not doi:
        ids = work.get("ids") or {}
        if isinstance(ids, dict):
            doi = ids.get("doi") or ids.get("DOI")
    return normalize_doi_iri(doi)