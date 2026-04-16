import argparse
import html
import json
import re
import sys
import unicodedata
from dataclasses import dataclass

TARGET_PREDICATE = "<https://schema.org/name>"
ACCENT_TO_COMBINING = {
    '"': "\u0308",
    "'": "\u0301",
    "’": "\u0301",
    "´": "\u0301",
    "`": "\u0300",
    "~": "\u0303",
    "c": "\u0327",
    "^": "\u0302",
    "v": "\u030C",
}
ACCENT_RE = re.compile(
    r"\{?\s*(?:\\\s*)+([\"'`~^cv’´])\s*(?:\{\s*(\\i|\\j|[^\W\d_])\s*\}|(\\i|\\j|[^\W\d_]))\s*\}?"
)
SPECIAL_CMD_MAP = {
    "ss": "ß",
    "SS": "ẞ",
    "o": "ø",
    "O": "Ø",
    "ae": "æ",
    "AE": "Æ",
    "oe": "œ",
    "OE": "Œ",
    "aa": "å",
    "AA": "Å",
    "l": "ł",
    "L": "Ł",
    "i": "i",
    "j": "j",
}
SPECIAL_CMD_RE = re.compile(r"\{?\s*(?:\\\s*)+(ss|SS|ae|AE|oe|OE|aa|AA|o|O|l|L|i|j)\s*\}?")
SINGLE_BRACE_RE = re.compile(r"\{([^{}\s])\}")
ENTITY_ESCAPE_RE = re.compile(r"\\+(?=&(?:#\d+|#x[0-9A-Fa-f]+|[A-Za-z]+);)")
NON_ASCII_MID_RE = re.compile(r"(?<=[A-Za-z])\s+([^\x00-\x7F])\s+(?=[A-Za-z])")
SPACING_CMD_RE = re.compile(r"\\\s+|\\[,;:!]")
ESCAPED_PUNCT_RE = re.compile(r"\\([&#%_{}])")
BROKEN_TAIL_RE = re.compile(r"\s*\{?\s*(?:\\\s*)+(?:[\"'`~^cv’´])?\s*$")
INLINE_MATH_RE = re.compile(r"\\\((.*?)\\\)")
MATH_CMD_RE = re.compile(r"\\([A-Za-z]+)")
MATH_CMD_MAP = {
    "parallel": "∥",
}


@dataclass
class ParsedLine:
    subject: str
    predicate: str
    object_text: str
    obj_kind: str
    lit_lex: str | None
    lit_suffix: str | None


@dataclass
class TransformResult:
    subject: str
    changed: bool
    dropped_corrupt: bool
    cleaned_line: str
    dedupe_key: str
    score: int
    before: str
    after: str


def parse_nt_line(line: str) -> ParsedLine | None:
    s = line.rstrip("\n")
    n = len(s)
    i = 0

    def skip_ws(idx: int) -> int:
        while idx < n and s[idx].isspace():
            idx += 1
        return idx

    def parse_iri(idx: int) -> tuple[str, int] | None:
        if idx >= n or s[idx] != "<":
            return None
        j = s.find(">", idx + 1)
        if j == -1:
            return None
        return s[idx : j + 1], j + 1

    def parse_bnode(idx: int) -> tuple[str, int] | None:
        if not s.startswith("_:", idx):
            return None
        j = idx + 2
        while j < n and not s[j].isspace():
            j += 1
        if j == idx + 2:
            return None
        return s[idx:j], j

    def parse_literal(idx: int) -> tuple[str, str, int] | None:
        if idx >= n or s[idx] != '"':
            return None
        j = idx + 1
        while j < n:
            c = s[j]
            if c == "\\":
                j += 2
                continue
            if c == '"':
                break
            j += 1
        if j >= n or s[j] != '"':
            return None
        lex = s[idx + 1 : j]
        k = j + 1
        suffix = ""
        if k < n and s[k] == "@":
            m = k + 1
            while m < n and (s[m].isalnum() or s[m] == "-"):
                m += 1
            suffix = s[k:m]
            k = m
        elif k + 1 < n and s[k : k + 2] == "^^":
            iri = parse_iri(k + 2)
            if iri is None:
                return None
            iri_text, iri_end = iri
            suffix = "^^" + iri_text
            k = iri_end
        return lex, suffix, k

    def parse_object(idx: int) -> tuple[str, str | None, str | None, int, str] | None:
        iri = parse_iri(idx)
        if iri is not None:
            text, end = iri
            return text, None, None, end, "iri"
        bnode = parse_bnode(idx)
        if bnode is not None:
            text, end = bnode
            return text, None, None, end, "bnode"
        lit = parse_literal(idx)
        if lit is not None:
            lex, suffix, end = lit
            text = f'"{lex}"{suffix}'
            return text, lex, suffix, end, "literal"
        return None

    i = skip_ws(i)
    subj = parse_iri(i) or parse_bnode(i)
    if subj is None:
        return None
    subject, i = subj
    i = skip_ws(i)
    pred = parse_iri(i)
    if pred is None:
        return None
    predicate, i = pred
    i = skip_ws(i)
    obj = parse_object(i)
    if obj is None:
        return None
    object_text, lit_lex, lit_suffix, i, obj_kind = obj
    i = skip_ws(i)
    if i >= n or s[i] != ".":
        return None
    i += 1
    i = skip_ws(i)
    if i != n:
        return None
    return ParsedLine(subject, predicate, object_text, obj_kind, lit_lex, lit_suffix)


def unescape_nt_literal(text: str) -> str:
    out: list[str] = []
    i = 0
    n = len(text)
    simple = {
        "t": "\t",
        "b": "\b",
        "n": "\n",
        "r": "\r",
        "f": "\f",
        '"': '"',
        "'": "'",
        "\\": "\\",
    }
    while i < n:
        c = text[i]
        if c != "\\" or i + 1 >= n:
            out.append(c)
            i += 1
            continue
        nxt = text[i + 1]
        if nxt in simple:
            out.append(simple[nxt])
            i += 2
            continue
        if nxt == "u" and i + 5 < n:
            h = text[i + 2 : i + 6]
            if all(ch in "0123456789abcdefABCDEF" for ch in h):
                out.append(chr(int(h, 16)))
                i += 6
                continue
        if nxt == "U" and i + 9 < n:
            h = text[i + 2 : i + 10]
            if all(ch in "0123456789abcdefABCDEF" for ch in h):
                out.append(chr(int(h, 16)))
                i += 10
                continue
        out.append("\\")
        out.append(nxt)
        i += 2
    return "".join(out)


def escape_nt_literal(text: str) -> str:
    out: list[str] = []
    for ch in text:
        o = ord(ch)
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\b":
            out.append("\\b")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\f":
            out.append("\\f")
        elif o < 0x20 or o == 0x7F:
            out.append(f"\\u{o:04X}")
        else:
            out.append(ch)
    return "".join(out)


def apply_accent(mark_cmd: str, base: str) -> str:
    mark = ACCENT_TO_COMBINING.get(mark_cmd)
    if mark is None:
        return "\\" + mark_cmd + base
    return unicodedata.normalize("NFC", base + mark)


def convert_latex_accents(text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        base = match.group(2) or match.group(3)
        if not base:
            return match.group(0)
        base = base.strip()
        if base.startswith("{") and base.endswith("}") and len(base) >= 3:
            base = base[1:-1].strip()
        if base in ("\\i", "i"):
            base = "i"
        elif base in ("\\j", "j"):
            base = "j"
        elif base == "ı":
            base = "i"
        elif len(base) != 1 or not base.isalpha():
            return match.group(0)
        return apply_accent(match.group(1), base)

    s = text
    for _ in range(3):
        ns = ACCENT_RE.sub(repl, s)
        if ns == s:
            break
        s = ns
    return s


def convert_special_commands(text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        cmd = match.group(1)
        return SPECIAL_CMD_MAP.get(cmd, match.group(0))

    s = text
    for _ in range(3):
        ns = SPECIAL_CMD_RE.sub(repl, s)
        if ns == s:
            break
        s = ns
    return s


def decode_entities(text: str) -> str:
    s = ENTITY_ESCAPE_RE.sub("", text)
    return html.unescape(s)


def fix_space_artifacts(text: str) -> str:
    s = text
    for _ in range(4):
        ns = NON_ASCII_MID_RE.sub(r"\1", s)
        if ns == s:
            break
        s = ns
    return s


def normalize_simple_latex_commands(text: str) -> str:
    s = SPACING_CMD_RE.sub(" ", text)
    s = ESCAPED_PUNCT_RE.sub(r"\1", s)
    s = s.replace("\\\\", " ")
    s = re.sub(r"\s{2,}", " ", s).strip()
    return s


def trim_broken_tail(text: str) -> str:
    s = text
    for _ in range(2):
        ns = BROKEN_TAIL_RE.sub("", s)
        if ns == s:
            break
        s = ns.rstrip()
    return s


def convert_inline_math(text: str) -> str:
    def cmd_repl(match: re.Match[str]) -> str:
        cmd = match.group(1)
        return MATH_CMD_MAP.get(cmd, match.group(0))

    def inline_repl(match: re.Match[str]) -> str:
        inside = match.group(1)
        inside = MATH_CMD_RE.sub(cmd_repl, inside)
        inside = re.sub(r"\\\s+", " ", inside)
        return inside.strip()

    s = text
    for _ in range(2):
        ns = INLINE_MATH_RE.sub(inline_repl, s)
        if ns == s:
            break
        s = ns
    return s


def strip_leftover_braces(text: str) -> str:
    s = text
    for _ in range(4):
        ns = SINGLE_BRACE_RE.sub(r"\1", s).replace("{}", "")
        if ns == s:
            break
        s = ns
    return s


def is_corrupt_name(text: str) -> bool:
    if text.endswith("\\"):
        return True
    if text.count("{") != text.count("}"):
        return True
    if re.search(r"\{\s*[\"'`]", text) or re.search(r"[\"'`]\s*\}", text):
        return True
    n = len(text)
    i = 0
    while i < n:
        if text[i] != "\\":
            i += 1
            continue
        j = i
        while j < n and text[j] == "\\":
            j += 1
        if j >= n:
            return True
        cmd = text[j]
        if cmd not in ACCENT_TO_COMBINING:
            i = j + 1
            continue
        k = j + 1
        while k < n and text[k].isspace():
            k += 1
        if k >= n:
            return True
        if text[k] == "{":
            if k + 2 >= n or not text[k + 1].isalpha() or text[k + 2] != "}":
                return True
            i = k + 3
            continue
        if not text[k].isalpha():
            return True
        i = k + 1
    if "{\\" in text:
        return True
    if re.search(r"(?:^|[^\\])\\(?:$|[\"'`~^cv’´]\s*(?:$|[}\s]))", text):
        return True
    return False


def normalize_name_value(text: str) -> str:
    s = decode_entities(text)
    s = convert_inline_math(s)
    s = convert_latex_accents(s)
    s = convert_special_commands(s)
    s = normalize_simple_latex_commands(s)
    s = strip_leftover_braces(s)
    s = fix_space_artifacts(s)
    s = trim_broken_tail(s)
    return s


def name_score(text: str) -> int:
    non_ascii_letters = sum(1 for c in text if c.isalpha() and ord(c) > 127)
    has_latex_artifacts = any(ch in text for ch in "\\{}")
    letter_count = sum(1 for c in text if c.isalpha())
    score = len(text) + min(letter_count, 200)
    if non_ascii_letters > 0:
        score += 1000 + non_ascii_letters * 10
    if has_latex_artifacts:
        score -= 500
    if len(text.strip()) <= 3:
        score -= 300
    return score


def transform_schema_name(line: str, parsed: ParsedLine, drop_corrupt: bool) -> TransformResult | None:
    if parsed.predicate != TARGET_PREDICATE or parsed.obj_kind != "literal" or parsed.lit_lex is None:
        return None
    before_line = line.rstrip("\n")
    original_value = unescape_nt_literal(parsed.lit_lex)
    cleaned_value = normalize_name_value(original_value)
    corrupt = is_corrupt_name(cleaned_value)
    if corrupt and drop_corrupt:
        return TransformResult(
            subject=parsed.subject,
            changed=False,
            dropped_corrupt=True,
            cleaned_line="",
            dedupe_key="",
            score=-10**9,
            before=before_line,
            after="",
        )
    escaped = escape_nt_literal(cleaned_value)
    suffix = parsed.lit_suffix or ""
    obj = f'"{escaped}"{suffix}'
    cleaned_line = f"{parsed.subject} {parsed.predicate} {obj} .\n"
    changed = cleaned_value != original_value
    dedupe_key = f"{cleaned_value}\u241F{suffix}"
    return TransformResult(
        subject=parsed.subject,
        changed=changed,
        dropped_corrupt=False,
        cleaned_line=cleaned_line,
        dedupe_key=dedupe_key,
        score=name_score(cleaned_value),
        before=before_line,
        after=cleaned_line.rstrip("\n"),
    )


def add_example(items: list[dict], entry: dict, limit: int = 50) -> None:
    if len(items) < limit:
        items.append(entry)


def first_pass_best(input_path: str, dedupe: bool, drop_corrupt: bool) -> tuple[dict[str, str], int]:
    best_key_by_subject: dict[str, str] = {}
    best_score_by_subject: dict[str, int] = {}
    seen_keys_by_subject: dict[str, set[str]] = {}
    deduped = 0
    with open(input_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            parsed = parse_nt_line(line)
            if not parsed:
                continue
            tr = transform_schema_name(line, parsed, drop_corrupt)
            if tr is None or tr.dropped_corrupt:
                continue
            if dedupe:
                seen = seen_keys_by_subject.setdefault(tr.subject, set())
                if tr.dedupe_key in seen:
                    deduped += 1
                    continue
                seen.add(tr.dedupe_key)
            best_score = best_score_by_subject.get(tr.subject)
            if best_score is None or tr.score > best_score:
                best_score_by_subject[tr.subject] = tr.score
                best_key_by_subject[tr.subject] = tr.dedupe_key
    return best_key_by_subject, deduped


def process_single_pass(args: argparse.Namespace) -> dict:
    stats = {
        "total_lines": 0,
        "modified_names": 0,
        "dropped_corrupt_names": 0,
        "deduped_names": 0,
    }
    changed_examples: list[dict] = []
    dropped_examples: list[dict] = []
    seen_by_subject: dict[str, set[str]] = {}

    with open(args.input_path, "r", encoding="utf-8", errors="replace") as fin, open(
        args.output_path, "w", encoding="utf-8"
    ) as fout:
        for line in fin:
            stats["total_lines"] += 1
            parsed = parse_nt_line(line)
            if not parsed:
                fout.write(line)
                continue
            tr = transform_schema_name(line, parsed, args.drop_corrupt)
            if tr is None:
                fout.write(line)
                continue
            if tr.dropped_corrupt:
                stats["dropped_corrupt_names"] += 1
                add_example(dropped_examples, {"reason": "corrupt", "line": tr.before})
                continue
            if args.dedupe:
                seen = seen_by_subject.setdefault(tr.subject, set())
                if tr.dedupe_key in seen:
                    stats["deduped_names"] += 1
                    add_example(dropped_examples, {"reason": "deduped", "line": tr.before})
                    continue
                seen.add(tr.dedupe_key)
            if tr.changed:
                stats["modified_names"] += 1
                add_example(changed_examples, {"before": tr.before, "after": tr.after})
            fout.write(tr.cleaned_line)

    return {"counts": stats, "examples": {"modified": changed_examples, "dropped": dropped_examples}}


def process_keep_best(args: argparse.Namespace) -> dict:
    best_key_by_subject, deduped_from_scan = first_pass_best(args.input_path, args.dedupe, args.drop_corrupt)

    stats = {
        "total_lines": 0,
        "modified_names": 0,
        "dropped_corrupt_names": 0,
        "deduped_names": deduped_from_scan,
    }
    changed_examples: list[dict] = []
    dropped_examples: list[dict] = []
    emitted_subjects: set[str] = set()

    with open(args.input_path, "r", encoding="utf-8", errors="replace") as fin, open(
        args.output_path, "w", encoding="utf-8"
    ) as fout:
        for line in fin:
            stats["total_lines"] += 1
            parsed = parse_nt_line(line)
            if not parsed:
                fout.write(line)
                continue
            tr = transform_schema_name(line, parsed, args.drop_corrupt)
            if tr is None:
                fout.write(line)
                continue
            if tr.dropped_corrupt:
                stats["dropped_corrupt_names"] += 1
                add_example(dropped_examples, {"reason": "corrupt", "line": tr.before})
                continue
            best_key = best_key_by_subject.get(tr.subject)
            if best_key is None:
                add_example(dropped_examples, {"reason": "not-selected", "line": tr.before})
                continue
            if tr.subject in emitted_subjects or tr.dedupe_key != best_key:
                add_example(dropped_examples, {"reason": "not-selected", "line": tr.before})
                continue
            emitted_subjects.add(tr.subject)
            if tr.changed:
                stats["modified_names"] += 1
                add_example(changed_examples, {"before": tr.before, "after": tr.after})
            fout.write(tr.cleaned_line)

    return {"counts": stats, "examples": {"modified": changed_examples, "dropped": dropped_examples}}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--in", dest="input_path", required=True)
    parser.add_argument("--out", dest="output_path", required=True)
    parser.add_argument("--dedupe", action="store_true")
    parser.add_argument("--keep-best-only", action="store_true")
    parser.add_argument("--drop-corrupt", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--report")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.keep_best_only:
        report_data = process_keep_best(args)
    else:
        report_data = process_single_pass(args)

    counts = report_data["counts"]
    sys.stderr.write(
        "total_lines={total_lines} modified_names={modified_names} dropped_corrupt_names={dropped_corrupt_names} deduped_names={deduped_names}\n".format(
            **counts
        )
    )

    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            json.dump(report_data, f, ensure_ascii=False, indent=2)
            f.write("\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
