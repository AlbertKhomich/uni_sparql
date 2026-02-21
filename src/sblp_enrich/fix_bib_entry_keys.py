#!/usr/bin/env python3
import argparse
import html
import re
import shutil
import unicodedata
from pathlib import Path

try:
    from pylatexenc.latex2text import LatexNodes2Text  # type: ignore
except Exception:
    LatexNodes2Text = None


ENTRY_HEAD_RE = re.compile(r"^(\s*@\w+\s*[{(]\s*)(.*)$", re.UNICODE)
WS_RE = re.compile(r"\s+", re.UNICODE)
DISALLOWED_KEY_CHARS_RE = re.compile(r"[^\w:+./-]", re.UNICODE)
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
SPECIAL_CMD_RE = re.compile(
    r"\{?\s*(?:\\\s*)+(ss|SS|ae|AE|oe|OE|aa|AA|o|O|l|L|i|j)\s*\}?"
)
ESCAPED_PUNCT_RE = re.compile(r"\\([&#%_{}])")
SINGLE_BRACE_RE = re.compile(r"\{([^{}\s])\}")

_l2t = LatexNodes2Text() if LatexNodes2Text else None


def _apply_accent(mark_cmd: str, base: str) -> str:
    mark = ACCENT_TO_COMBINING.get(mark_cmd)
    if mark is None:
        return "\\" + mark_cmd + base
    return unicodedata.normalize("NFC", base + mark)


def _convert_latex_accents(text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        base = match.group(2) or match.group(3)
        if not base:
            return match.group(0)
        base = base.strip()
        if base in ("\\i", "i", "ı"):
            base = "i"
        elif base in ("\\j", "j"):
            base = "j"
        elif len(base) != 1 or not base.isalpha():
            return match.group(0)
        return _apply_accent(match.group(1), base)

    s = text
    for _ in range(3):
        ns = ACCENT_RE.sub(repl, s)
        if ns == s:
            break
        s = ns
    return s


def _convert_special_commands(text: str) -> str:
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


def _strip_diacritics(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _decode_latex_key(text: str) -> str:
    s = html.unescape(html.unescape(text))
    if _l2t is not None:
        try:
            s = _l2t.latex_to_text(s)
        except Exception:
            pass
    s = _convert_latex_accents(s)
    s = _convert_special_commands(s)
    s = ESCAPED_PUNCT_RE.sub(r"\1", s)
    for _ in range(4):
        ns = SINGLE_BRACE_RE.sub(r"\1", s).replace("{}", "")
        if ns == s:
            break
        s = ns
    return s


def _has_latex_markup(text: str) -> bool:
    return ("\\" in text) or ("{" in text) or ("}" in text)


def normalize_key(key: str) -> str:
    original = key.strip()
    normalized = WS_RE.sub("_", original)
    normalized = _decode_latex_key(normalized)
    normalized = normalized.replace("(", "").replace(")", "")
    normalized = normalized.replace(",", "")
    if _has_latex_markup(original):
        normalized = _strip_diacritics(normalized)
    # Keep only a conservative BibTeX-safe key character set.
    normalized = DISALLOWED_KEY_CHARS_RE.sub("", normalized)
    normalized = WS_RE.sub("_", normalized).strip("_")
    normalized = re.sub(r"_+", "_", normalized)
    return normalized


def _split_entry_start_line(line: str):
    m = ENTRY_HEAD_RE.match(line)
    if not m:
        return None

    prefix, rest = m.groups()
    for i, ch in enumerate(rest):
        if ch != ",":
            continue

        tail = rest[i + 1 :]
        tail_lstrip = tail.lstrip()
        if not tail_lstrip or tail_lstrip.startswith("\n") or tail_lstrip.startswith("\r"):
            return prefix, rest[:i], "," + tail

        fm = re.match(r"^([A-Za-z][A-Za-z0-9_-]*)\s*=", tail_lstrip)
        if fm:
            return prefix, rest[:i], "," + tail

    return None


def rewrite_text(text: str):
    changed = 0
    out_lines = []
    samples = []

    for line in text.splitlines(keepends=True):
        line_ending = ""
        content = line
        if content.endswith("\r\n"):
            content = content[:-2]
            line_ending = "\r\n"
        elif content.endswith("\n"):
            content = content[:-1]
            line_ending = "\n"
        elif content.endswith("\r"):
            content = content[:-1]
            line_ending = "\r"

        parts = _split_entry_start_line(content)
        if not parts:
            out_lines.append(line)
            continue

        prefix, key, suffix = parts
        fixed = normalize_key(key)
        if fixed != key:
            changed += 1
            if len(samples) < 10:
                samples.append((key, fixed))
            line = f"{prefix}{fixed}{suffix}{line_ending}"
        out_lines.append(line)

    return "".join(out_lines), changed, samples


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Normalize BibTeX entry keys: replace whitespace with underscores and "
            "decode/repair LaTeX artifacts in keys while dropping malformed symbols."
        )
    )
    parser.add_argument("bib_file", help="Path to .bib file")
    parser.add_argument(
        "-o",
        "--output",
        help="Write rewritten content to this file instead of editing in place",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not write files, only report how many keys would change",
    )
    parser.add_argument(
        "--backup-suffix",
        default=".bak",
        help="Backup suffix for in-place edits (default: .bak)",
    )
    args = parser.parse_args()

    in_path = Path(args.bib_file)
    text = in_path.read_text(encoding="utf-8", errors="replace")
    rewritten, changed, samples = rewrite_text(text)

    print(f"Entry keys updated: {changed}")
    for before, after in samples:
        print(f"- {before} -> {after}")

    if args.dry_run or changed == 0:
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
