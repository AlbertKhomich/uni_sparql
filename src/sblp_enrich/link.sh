#!/usr/bin/env bash
set -euo pipefail

IN="/data/uni_sparql/data/uni/uni_v8.nt"
OUT="/data/uni_sparql/data/uni/uni_v8_sameas.nt"

cat \
  <(sed -nE 's#^(<[^>]+>) <https://schema\.org/identifier> ("DOI:[^"]*")[[:space:]]*\.$#\1 <https://schema.org/doi> \2 .#p' "$IN") \
  <(sed -nE '/^<[^>]+> <https:\/\/schema\.org\/sameAs> <[^>]+> \.$/p' "$IN") \
| awk '
{
  subj = $1

  # extract object by removing "<subj> <pred> " from start, and trailing " ." from end
  line = $0
  sub(/^[^[:space:]]+[[:space:]]+<[^>]+>[[:space:]]+/, "", line)
  sub(/[[:space:]]+\.[[:space:]]*$/, "", line)
  obj = line

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
