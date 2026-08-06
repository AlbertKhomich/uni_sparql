#!/usr/bin/env bash

set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "Usage: $0 <input-file>"
    exit 1
fi

input_file="$1"
output_file="${input_file%.*}_updated.${input_file##*.}"

sed 's|https://dice-research\.org|http://upbkg.data.dice-research.org|g' \
    "$input_file" > "$output_file"

echo "Saved to: $output_file"