import re
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

def pick_best_authorship(authorships: List[Dict], person_name: str) -> Optional[Dict]:
    pl, pi = last_and_initial(person_name)

    best = None
    best_score = -1.0

    for au in authorships:
        author = (au.get("author") or {})
        dn = author.get("display_name") or ""
        al, ai = last_and_initial(dn)

        score = 0.0
        if pl and al and pl == al:
            score += 0.6
            if pi and ai and pi == ai:
                score += 0.2

        score += 0.2 * sim(person_name, dn)

        if score > best_score:
            best_score = score
            best = au

    if best is None or best_score < 0.55:
        return None
    return best

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