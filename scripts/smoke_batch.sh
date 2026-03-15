#!/usr/bin/env bash
set -euo pipefail

ENDPOINT="${SMOKE_ENDPOINT:-http://localhost:8000/v1/responses}"
INPUT_DIR="${SMOKE_INPUT_DIR:-tests/fixtures/rename_inputs}"
OUT="${SMOKE_OUT:-.respkit_smoke}"

rm -rf "${OUT}"
mkdir -p "${OUT}"
python3 -m examples.run_rename_proposal batch "${INPUT_DIR}" --endpoint "${ENDPOINT}" --out "${OUT}"
