#!/usr/bin/env bash
set -euo pipefail

ENDPOINT="${SMOKE_ENDPOINT:-http://localhost:8000/v1/responses}"
INPUT_DIR="${SMOKE_INPUT_DIR:-tests/fixtures/rename_inputs}"
OUT="${SMOKE_OUT:-.respkit_smoke}"
MAX_CONCURRENCY="${SMOKE_MAX_CONCURRENCY:-1}"
REVIEW_MAX_CONCURRENCY="${SMOKE_REVIEW_MAX_CONCURRENCY:-1}"
PROVIDER_TIMEOUT="${SMOKE_PROVIDER_TIMEOUT:-30}"
REVIEW="${SMOKE_REVIEW:-}"

rm -rf "${OUT}"
mkdir -p "${OUT}"
COMMAND_ARGS=(
  python3 -m examples.demo_rename_proposal batch
  "${INPUT_DIR}"
  --endpoint "${ENDPOINT}"
  --out "${OUT}"
  --max-concurrency "${MAX_CONCURRENCY}"
  --review-max-concurrency "${REVIEW_MAX_CONCURRENCY}"
  --provider-timeout "${PROVIDER_TIMEOUT}"
)

if [ -n "${REVIEW}" ]; then
    COMMAND_ARGS+=(--review)
fi

"${COMMAND_ARGS[@]}"
