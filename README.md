# respkit

RespKit is a small, reusable Python SDK for structured, prompt-driven LLM tasks over normalized text inputs.

V1 is intentionally narrow:
- text-only input
- OpenAI-compatible Responses API only (single provider adapter)
- one-item execution + batch wrappers built from it
- schema validation + deterministic repair
- deterministic filesystem artifacts
- append-only JSONL manifest
- optional review pass

## Repository Layout

- `respkit/`
  - `providers/` — provider interface and OpenAI-compatible adapter
  - `inputs/` — normalized input model
  - `prompts/` — markdown templates + simple renderer
  - `contracts/` — schema/validation report helpers
  - `validators/` — deterministic validators + repair helpers
  - `actions/` — side-effect actions (markdown, json, manifest)
  - `artifacts/` — per-run artifact writer and policy
  - `manifest/` — append-only JSONL manifest writer
  - `tasks/` — task definitions and result models
  - `runners/` — single-item, batch, and review runners
  - `review/` — review package placeholder
  - `utils/` — small helpers (run id, filesystem)
- `examples/`
  - `rename_file_proposal/` — complete example task (with prompts and schemas)
  - `run_rename_proposal.py` — tiny example entrypoint
- `tests/` — unit tests for v1 execution surface

## Implemented in v1

- `NormalizedInput` for one item of text text input
- `LLMProvider` interface and `OpenAICompatibleProvider`
- markdown prompt loading/rendering with light variable interpolation
- typed contracts via Pydantic models
- deterministic validators and fill/trim/enum normalization
- task definitions with prompt reference, schema, model config, validators, actions
- actions for markdown + JSON artifact write + manifest append
- per-run artifact snapshots (`prompt`, `raw_response`, `validated_response`, `validation_report`, `action_results`, metadata)
- append-only manifest rows
- `SingleInputRunner`
- `DirectoryBatchRunner` built on single runner
- `ReviewRunner`
- optional review wiring in task definitions

## What is intentionally left out in v1

- no multimodal inputs
- no automatic prompt rewriting
- no multi-provider routing
- no MCP integration
- no workflow orchestration layer beyond small runners

## Add your own tasks

Create a task module under `examples/` or your own package:

1. Define Pydantic output schema.
2. Write markdown prompt template.
3. Build a `TaskDefinition` with:
   - prompt path
   - response model
   - provider model name
   - optional validators/actions
   - optional `ReviewPolicy`

Then call a `SingleInputRunner` with your task.

## Running the example task

```bash
python -m examples.run_rename_proposal single /path/to/text.txt --endpoint http://localhost:8000/v1/responses --out .respkit_demo
python -m examples.run_rename_proposal batch /path/to/text-directory --endpoint http://localhost:8000/v1/responses --out .respkit_demo --review
```

You can add `--review` to perform the optional second-pass review with the companion task.

Common tuning flags:

- `--max-concurrency N` (proposal phase batch parallelism)
- `--review-max-concurrency N` (review phase parallelism; default `1`)
- `--provider-timeout S` (provider request timeout in seconds)

Example tuned run:

```bash
python -m examples.run_rename_proposal batch /path/to/files \
  --endpoint http://localhost:8000/v1/responses \
  --out .respkit_demo \
  --review \
  --max-concurrency 8 \
  --review-max-concurrency 4 \
  --provider-timeout 30
```

## Local smoke test

Use the fixtures in `tests/fixtures/rename_inputs/` and run against a local endpoint:

```bash
make smoke-single      # runs one file at tests/fixtures/rename_inputs/clean_easy.txt
make smoke-batch       # runs all local fixture inputs
make smoke             # runs single then batch
```

The smoke targets use `--endpoint http://localhost:8000/v1/responses` by default and write artifacts to `.respkit_smoke`.

You can override:

```bash
make smoke-single SMOKE_ENDPOINT=http://localhost:8000/v1/responses SMOKE_OUT=tmp/smoke
make smoke-batch SMOKE_MAX_CONCURRENCY=8 SMOKE_REVIEW_MAX_CONCURRENCY=4 SMOKE_PROVIDER_TIMEOUT=30 SMOKE_REVIEW=1 SMOKE_OUT=tmp/smoke
```

## Stable run statuses

Status values are fixed across runners, run metadata, and manifests:

- `success`
- `preflight_model_not_found`
- `provider_error`
- `parse_error`
- `validation_failed`
- `action_failed`
- `review_failed`

`parse_error` is used only when the provider output cannot be parsed into a structured JSON payload.  
`validation_failed` is used when the payload parses but does not pass schema/validator checks.

## Batch summary

Batch runs now write `batch_summary.json` in the batch output root:

```bash
make smoke-batch SMOKE_OUT=.respkit_smoke
cat .respkit_smoke/batch_summary.json
```

The command also prints a concise status summary to stdout at end-of-batch.

## Corpus export

Run a directory and export `source_path,status,kind,actor,slug,confidence`:

```bash
make corpus-eval CORPUS_DIR=tests/fixtures/rename_inputs CORPUS_FORMAT=csv CORPUS_EXPORT=tmp/corpus_eval.csv
```

or directly:

```bash
python3 scripts/evaluate_corpus.py tests/fixtures/rename_inputs --format csv --export tmp/corpus_eval.csv
```

By default, `scripts/evaluate_corpus.py` reads existing proposal artifacts from `--out` and does **not** re-run the provider.
Add `--rerun` only when you explicitly want to regenerate outputs from the model:

```bash
python3 scripts/evaluate_corpus.py tests/fixtures/rename_inputs --out .respkit_runs/live --rerun --format csv --export tmp/corpus_eval.csv
```

## Review interpretation

Review output is intentionally narrow:

- `pass` -> accepted
- `uncertain` -> `review_failed` status, send to human review
- `fail` -> `review_failed` status, do not auto-accept

## Artifact layout

Each run writes the following files under `artifacts/<task_name>/<run_id>/`:

- `prompt_template.md` — source template snapshot
- `prompt.txt` — rendered prompt
- `provider_request.json` — request payload sent to the provider
- `raw_response.json` — raw provider response
- `discovered_models.json` — discovered model ids from `/v1/models` preflight
- `parsed_response.json` — parsed JSON payload when available
- `validation_report.json` — normalized validation outcome
- `validated_response.json` — validated output after schema/validator pass
- `action_results.json` — action execution summaries
- `run_metadata.json` — run metadata and status
- `manifest_row.json` — optional row writer output (if manifest action is used)

## If you get model-not-found

- Verify the model IDs exposed by your endpoint:
  - `curl http://localhost:8000/v1/models`
- Use exactly one of the returned model IDs in your task configuration (example task uses `gpt-oss-20b`).
- If the endpoint exposes a different serve name, start vLLM with:
  - `--served-model-name gpt-oss-20b`

## Example structure summary

- Proposal schema: `examples/rename_file_proposal/schemas.py`
- Example task definitions: `examples/rename_file_proposal/task.py`
- Templates:
  - `examples/rename_file_proposal/prompts/rename_file_proposal.md`
  - `examples/rename_file_proposal/prompts/rename_file_review.md`

## Notes

The review task is intentionally small:
- input uses original text + serialized first-pass output in metadata
- output is `{decision: pass|fail|uncertain, notes, recommended_adjustments}`

This first iteration keeps behavior explicit and avoids framework-heavy patterns so new tasks can be added by editing task definitions only.

## Troubleshooting local endpoints

- If the response parser never captures fields, lower temperature and ensure the endpoint is returning clean JSON.
- If the endpoint logs warnings about unsupported request fields, confirm `response_format` is not sent to `/v1/responses` and that the model is returning text that parses as JSON.
- If runs fail with request errors, inspect `provider_request.json` and `raw_response.json` for URL mismatches (`/v1/responses` vs `/responses`), headers, and payload shape.
- If runs return `validation_failed` on every file, inspect `validation_report.json` to see whether the issue is provider parse failure, schema mismatch, or task validators.

## Concurrency notes

Run directory batches with bounded concurrency:

```bash
python -m examples.run_rename_proposal batch /path/to/files --max-concurrency 4
python -m examples.run_rename_proposal batch /path/to/files --max-concurrency 8 --review --review-max-concurrency 4
```

Start with `2` or `4`, then increase cautiously and monitor local endpoint stability.
