# respkit

`respkit` is a small reusable Python SDK for structured LLM tasks over normalized text input.

## What the SDK contains

- `respkit/` — reusable core:
  - providers (`openai_compatible`, base contract)
  - runners (`single`, `batch`, `review`)
  - tasks/contracts
  - validators and normalization
  - actions (markdown/json/manifest)
  - artifacts + manifest writers
- `examples/` — safe synthetic examples only
- `tests/` — SDK tests with synthetic fixtures

## Supported shape

Every task uses:

- normalized input (`source_id`, `source_path`, `decoded_text`)
- prompt template + renderer
- schema validation
- deterministic action execution
- optional review pass

The core execution path remains generic and reusable:

- prompt rendering
- provider call
- response parsing + validation
- artifact capture
- manifest append
- optional review runner with optional concurrency

## Synthetic developer example

The public example is intentionally synthetic:

- `examples/demo_rename_proposal/`
  - `task.py` — task wiring and prompt/context builders
  - `schemas.py` — proposal/review output models
  - `prompts/` — synthetic prompts
  - `__main__.py` — CLI entrypoint

This example uses only synthetic names/entities and works without corpus-specific data.

## Run the example

```bash
python -m examples.demo_rename_proposal single /path/to/file.txt \
  --endpoint http://localhost:8000/v1/responses \
  --out .respkit_demo \
  --provider-timeout 30

python -m examples.demo_rename_proposal batch /path/to/text-dir \
  --endpoint http://localhost:8000/v1/responses \
  --out .respkit_demo \
  --max-concurrency 4 \
  --review
```

You can run the review pass concurrently with:

```bash
python -m examples.demo_rename_proposal batch /path/to/text-dir \
  --endpoint http://localhost:8000/v1/responses \
  --out .respkit_demo \
  --max-concurrency 8 \
  --review --review-max-concurrency 4 \
  --provider-timeout 30
```

Available flags:

- `--max-concurrency`: proposal batch parallelism
- `--review-max-concurrency`: review concurrency (default `1`)
- `--provider-timeout`: request timeout seconds
- `--review`: enable optional review pass

## Smoke scripts

```bash
make smoke-single   # single fixture
make smoke-batch    # batch fixtures
make smoke          # runs both
```

Env vars used by smoke targets:

- `SMOKE_ENDPOINT` (default `http://localhost:8000/v1/responses`)
- `SMOKE_MAX_CONCURRENCY` (default `1`)
- `SMOKE_REVIEW_MAX_CONCURRENCY` (default `1`)
- `SMOKE_PROVIDER_TIMEOUT` (default `30`)
- `SMOKE_REVIEW` (set to any non-empty value to enable review)

`scripts/smoke_single.sh` and `scripts/smoke_batch.sh` call the synthetic example by default and can be used outside `make`.

## Status vocabulary

Status values are consistent across runners and manifest rows:

- `success`
- `preflight_model_not_found`
- `provider_error`
- `parse_error`
- `validation_failed`
- `action_failed`
- `review_failed`

`parse_error` means the provider output could not be parsed into a JSON payload.

## Artifact output

Each task run writes per-item artifacts under:

```
.respkit_demo/
  artifacts/
    <task_name>/<run_id>/
      prompt_template.md
      prompt.txt
      provider_request.json
      raw_response.json
      parsed_response.json
      validation_report.json
      validated_response.json
      action_results.json
      run_metadata.json
      manifest_row.json (if manifest action is configured)
```

The run metadata includes provider timing, status, and chosen model.

`manifest.jsonl` is append-only and one row is written per manifest action invocation.

## Local test fixtures

`tests/fixtures/rename_inputs/*.txt` contains synthetic, non-sensitive material for local runs.

## Notes

This repository intentionally does not bundle real corpus data or private task iterations.
Those should live in a private task/corpus repo.

