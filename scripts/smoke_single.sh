#!/usr/bin/env bash
set -euo pipefail

ENDPOINT="${SMOKE_ENDPOINT:-http://localhost:8000/v1/responses}"
INPUT="${SMOKE_INPUT_FILE:-tests/fixtures/rename_inputs/clean_easy.txt}"
OUT="${SMOKE_OUT:-.respkit_smoke}"

rm -rf "${OUT}"
mkdir -p "${OUT}"
python3 -m examples.run_rename_proposal single "${INPUT}" --endpoint "${ENDPOINT}" --out "${OUT}"
