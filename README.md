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
python -m examples.run_rename_proposal single /path/to/text.txt --endpoint http://localhost:8000/v1 --out .respkit_demo
python -m examples.run_rename_proposal batch /path/to/text-directory --endpoint http://localhost:8000/v1 --out .respkit_demo --review
```

You can add `--review` to perform the optional second-pass review with the companion task.

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
