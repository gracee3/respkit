# respkit

`respkit` is a small reusable Python SDK for structured LLM tasks over normalized text input.

## Install

```bash
python3 -m pip install respkit
```

For local development:

```bash
git clone https://github.com/gracee3/respkit.git
cd respkit
python3 -m pip install -e .[dev]
```

## Quick Start

```python
from pathlib import Path

from respkit.inputs import NormalizedInput
from respkit.providers import OpenAICompatibleProvider
from respkit.runners import SingleInputRunner

from examples.demo_rename_proposal.task import build_tasks


proposal_task, _review_task = build_tasks()
runner = SingleInputRunner(
    task=proposal_task,
    provider=OpenAICompatibleProvider(endpoint="http://localhost:8000/v1/responses"),
    artifacts_root=Path(".respkit_demo"),
)

item = NormalizedInput(
    source_id="sample.txt",
    source_path=Path("sample.txt"),
    media_type="text/plain",
    decoded_text=Path("sample.txt").read_text(encoding="utf-8"),
)

result = runner.run(item)
print(result.status, result.validated_output)
```

`result` contains normalized status, structured output, validation report,
and artifact directory path. Use `ReviewRunner` when you need a second-pass
validator.

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

For a generic SDK-level example of the new corpus adjudication ledger abstraction, use:

```bash
python examples/demo_ledger.py
```

## Run the example

```bash
respkit-demo single /path/to/file.txt \
  --endpoint http://localhost:8000/v1/responses \
  --out .respkit_demo \
  --provider-timeout 30
  # or: python -m examples.demo_rename_proposal single /path/to/file.txt ...

respkit-demo batch /path/to/text-dir \
  --endpoint http://localhost:8000/v1/responses \
  --out .respkit_demo \
  --max-concurrency 4 \
  --review
```

You can run the review pass concurrently with:

```bash
respkit-demo batch /path/to/text-dir \
  --endpoint http://localhost:8000/v1/responses \
  --out .respkit_demo \
  --max-concurrency 8 \
  --review --review-max-concurrency 4 \
  --provider-timeout 30
  # or: python -m examples.demo_rename_proposal batch ...
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

## Ledger API (SDK)

`respkit` also ships a generic corpus adjudication ledger for tasks that iterate over many items and need proposal/review/human/apply coordination.

- module: `respkit.ledger`
- storage: JSONL append-only (`LedgerStore`)
- machine/human state is split:
  - machine: `not_run`, `proposed`, `reviewed`, `provider_error`, `apply_ready`, `applied`, `superseded`
  - human: `needs_review`, `approved`, `rejected`
- optional task-specific payloads stored in `extras: dict[str, Any]`
- optional apply hooks with optional clean-tree guard
- per-stage provenance and run identifiers
- `applied_in_commit` is separate from `apply_code_commit`:
  - `apply_code_commit`: code hash when apply handler is invoked
  - `applied_in_commit`: commit hash where resulting filesystem changes are captured, if available

### Core API

- `LedgerStore(ledger_path)` — create/open a ledger
- `create_or_update_row(...)` — create or update shared metadata for an item
- `record_proposal(...)` — write proposal payload/result and status
- `record_review(...)` — write review payload/result and status
- `record_human_decision(...)` — write human decision and transition state
- `record_apply(...)` — write apply payload/result and transition state
- `mark_superseded(...)` — mark historical rows as superseded
- `query_rows(LedgerQuery(...))` — select rows for review/retry/apply planning
- `run_apply(...)` — execute optional apply callback with dry-run and clean-tree policy options
- `export_csv(path, query=...)` — produce human-review friendly CSV

### Query examples

- unresolved only: `LedgerQuery(task_name=task_name, unresolved_only=True)`
- provider errors only: `LedgerQuery(provider_error_only=True)`
- rejected only: `LedgerQuery(rejected_only=True)`
- not approved only: `LedgerQuery(not_approved_only=True)`
- only unresolved & rerun eligible: `LedgerQuery(unresolved_only=True, rerun_eligible_only=True)`
- include/exclude controls:
  - `LedgerQuery(include_approved=False)`
  - `LedgerQuery(include_superseded=True)`

Minimal example:

```python
from pathlib import Path

from respkit.ledger import (
    ApplyPolicy,
    HumanDecision,
    LedgerQuery,
    LedgerStore,
    MachineStatus,
)

ledger = LedgerStore(Path(".my_ledger.jsonl"))
task_name = "generic-corpus-task"

row = ledger.record_proposal(
    task_name=task_name,
    item_id="item-001",
    item_locator="docs/file-a.txt",
    proposal_payload={"op": "normalize_section_headers"},
    proposal_result={"status": "ok"},
)

row = ledger.record_review(
    task_name=task_name,
    item_id="item-001",
    review_payload={"risk": "low"},
    review_result={"accept": True},
)

row = ledger.record_human_decision(
    task_name=task_name,
    item_id="item-001",
    decision=HumanDecision.APPROVED,
)

ready = ledger.query_rows(
    LedgerQuery(task_name=task_name, unresolved_only=True, include_approved=False)
)
print([r.item_id for r in ready])  # e.g. ["item-001"]

apply_results = ledger.run_apply(
    query=LedgerQuery(task_name=task_name, unresolved_only=True),
    callback=lambda _row, dry_run: (
        {"op": "apply"} if dry_run else {"op": "apply"},
        {"status": "ok"},
    ),
    dry_run=True,
)

# Example of guarded non-dry-run apply (will require clean working tree when enabled)
ledger.run_apply(
    query=LedgerQuery(task_name=task_name, unresolved_only=True),
    callback=lambda _row, dry_run: ({"op": "apply"}, {"status": "applied"}),
    dry_run=False,
    policy=ApplyPolicy(require_clean_working_tree=True, working_directory=Path(".")),
)
```

Example fields in one row include:

- `task_name`, `item_id`, `item_locator`, `input_fingerprint`, `rerun_eligible`
- `proposal_payload`/`review_payload`/`apply_payload` and result fields
- `proposal_run_id`/`review_run_id`/`human_decision_run_id`/`apply_run_id`
- `proposal_code_commit`/`review_code_commit`/`human_decision_code_commit`/`apply_code_commit`
- `applied_in_commit` (commit captured after mutation is observed, if enabled)
- timestamp fields (`created_at`, `updated_at`, stage-specific recorded times)

### Stage-level provenance

- commit fields capture the ledger code provenance at the time each stage is recorded:
  - `proposal_code_commit`
  - `review_code_commit`
  - `human_decision_code_commit`
  - `apply_code_commit`
- `applied_in_commit` is reserved for capturing the commit that contains code changes resulting from apply output (for example, after your external workflow commits files).

### Demo command

Run the generic ledger demo:

```bash
PYTHONPATH=. python3 examples/demo_ledger.py
```

Target explicit paths:

```bash
PYTHONPATH=. python3 examples/demo_ledger.py --repo /tmp/corpus_repo --ledger /tmp/corpus_ledger.jsonl
```

## Local test fixtures

`tests/fixtures/rename_inputs/*.txt` contains synthetic, non-sensitive material for local runs.

## Notes

This repository intentionally does not bundle real corpus data or private task iterations.
Those should live in a private task/corpus repo.
