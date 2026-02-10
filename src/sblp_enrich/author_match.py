import re
import unicodedata
from rapidfuzz import fuzz
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Tuple

def norm_name(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s

def last_and_initial(s: str) -> Tuple[str, str]:
    s = norm_name(s)
    if "," in s:
        last, rest = [p.strip() for p in s.split(",", 1)]
        ini = (rest[:1] if rest else "")
        return last, ini
    parts = s.split()
    if not parts:
        return "", ""
    last = parts[-1]
    ini = parts[0][:1] if parts[0] else ""
    return last, ini

def sim(a: str, b: str) -> float:
    return SequenceMatcher(None, norm_name(a), norm_name(b)).ratio()

def only_letters(name: str) -> str:
    s = (name or "").strip().lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))

    return re.sub(r"[^a-z]", "", s)

def pick_best_authorship(authorships: List[Dict], person_name: str) -> Optional[Dict]:
    for au in authorships:
        author = (au.get("author") or {})
        dn = author.get("display_name") or ""
        
        last_sim = fuzz.token_sort_ratio(person_name, dn)
        if last_sim >= 75:
            return au
    
    return None

def pick_best_work_by_title(query_title: str, results: list[dict], min_sim: float = 0.80) -> dict | None:
    best = None
    best_s = -1.0
    for r in results:
        cand_title = r.get("display_name") or r.get("title") or ""
        s = sim(query_title, cand_title)
        if s > best_s:
            best_s = s
            best = r
    return best if best and best_s >= min_sim else None