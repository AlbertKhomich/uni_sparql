#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage:
  sameas_pairs.sh -i INPUT.nt -o OUTPUT.tsv
  sameas_pairs.sh INPUT.nt OUTPUT.tsv

Creates subject-subject pairs (tab-separated) for subjects that share the same object
(from schema:sameAs triples and identifier "DOI:..." rewritten as schema:doi).

Output format:
  <subj1>\t<subj2>\t1.0
EOF
  exit 2
}

IN=""
OUT=""

# Accept either flags (-i/-o) or positional (IN OUT)
if [[ $# -eq 2 && "$1" != -* && "$2" != -* ]]; then
  IN="$1"
  OUT="$2"
else
  while getopts ":i:o:h" opt; do
    case "$opt" in
      i) IN="$OPTARG" ;;
      o) OUT="$OPTARG" ;;
      h) usage ;;
      :) echo "Error: -$OPTARG requires an argument." >&2; usage ;;
      \?) echo "Error: unknown option -$OPTARG" >&2; usage ;;
    esac
  done
  shift $((OPTIND - 1))
  [[ $# -eq 0 ]] || { echo "Error: unexpected extra args: $*" >&2; usage; }
fi

[[ -n "${IN}" && -n "${OUT}" ]] || usage
[[ -f "${IN}" ]] || { echo "Error: input not found: $IN" >&2; exit 1; }

cat \
  <(sed -nE 's#^(<[^>]+>)[[:space:]]+<https://schema\.org/identifier>[[:space:]]+("DOI:[^"]*")[[:space:]]*\.[[:space:]]*$#\1 <https://schema.org/doi> \2 .#p' "$IN") \
  <(sed -nE '/^<[^>]+>[[:space:]]+<https:\/\/schema\.org\/sameAs>[[:space:]]+<[^>]+>[[:space:]]*\.[[:space:]]*$/p' "$IN") \
| tr -d '\r' \
| awk '
function norm_obj(o, doi, post) {
  # IRI form: <https://doi.org/...> or <http://doi.org/...>
  if (o ~ /^<https?:\/\/doi\.org\/[^>]+>$/) {
    doi = o
    sub(/^<https?:\/\/doi\.org\//, "", doi)
    sub(/>$/, "", doi)
    return "<https://doi.org/" tolower(doi) ">"
  }

  # Literal form: "DOI:...." (optionally with @lang or ^^<dt>)
  if (o ~ /^"DOI:[^"]*"/) {
    doi = o
    sub(/^"DOI:/, "", doi)
    sub(/".*$/, "", doi)
    post = o
    sub(/^"DOI:[^"]*"/, "", post)
    return "\"DOI:" tolower(doi) "\"" post
  }

  return o
}

{
  subj = $1

  # extract object by removing "<subj> <pred> " from start, and trailing " ." from end
  line = $0
  sub(/^[^[:space:]]+[[:space:]]+<[^>]+>[[:space:]]+/, "", line)
  sub(/[[:space:]]+\.[[:space:]]*$/, "", line)
  obj = norm_obj(line)

  # de-duplicate subject per object
  key = obj SUBSEP subj
  if (!seen[key]++) {
    n[obj]++
    list[obj SUBSEP n[obj]] = subj
  }
}
END {
  for (obj in n) {
    if (n[obj] < 2) continue
    for (i=1; i<n[obj]; i++)
      for (j=i+1; j<=n[obj]; j++)
        print list[obj SUBSEP i] "\t" list[obj SUBSEP j] "\t1.0"
  }
}
' > "$OUT"

echo "Wrote: $OUT"
