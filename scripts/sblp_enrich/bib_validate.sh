#!/usr/bin/env bash
set -euo pipefail

exec biber --tool --validate-datamodel "$@"
