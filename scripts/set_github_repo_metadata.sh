#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${GITHUB_TOKEN:-}" ]]; then
  echo "Set GITHUB_TOKEN first (repo:write scope required)." >&2
  echo "Example command you can run:"
  echo "  GITHUB_TOKEN=... $0"
  exit 1
fi

: "${GITHUB_OWNER:=gracee3}"
: "${GITHUB_REPO:=respkit}"

DESCRIPTION="Minimal reusable SDK for structured LLM tasks over normalized text input."
TOPICS=(llm sdk structured-output batch-processing openai-compatible manifest)

curl -fsSL -X PATCH \
  -H "Authorization: token ${GITHUB_TOKEN}" \
  -H "Accept: application/vnd.github+json" \
  "https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}" \
  -d "{\"description\": \"${DESCRIPTION}\"}"

curl -fsSL -X PUT \
  -H "Authorization: token ${GITHUB_TOKEN}" \
  -H "Accept: application/vnd.github+json" \
  "https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/topics" \
  -d "{\"names\": [\"${TOPICS[*]// /\",\"}\"]}"

echo "Updated GitHub repo metadata for ${GITHUB_OWNER}/${GITHUB_REPO}."
