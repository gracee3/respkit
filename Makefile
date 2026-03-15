# Local GPT-OSS 20B via vLLM + optional Open WebUI
# Single-file, slim setup for one or two RTX 3090s.

# ── Images ──────────────────────────────────────────────
IMAGE := vllm/vllm-openai:latest
OPEN_WEBUI_IMAGE := ghcr.io/open-webui/open-webui:main

# ── Model / paths ───────────────────────────────────────
MODEL_PATH ?= /data/models/openai/gpt-oss-20b
CACHE_PATH := $(HOME)/.cache/vllm
OPEN_WEBUI_DATA := $(HOME)/.local/share/open-webui

# ── Networking / ports ──────────────────────────────────
PORT ?= 8000
OPEN_WEBUI_PORT ?= 3002
DOCKER_NETWORK ?= local-inference-net
DOCKER_RESTART_POLICY ?= no
DOCKER_PULL_POLICY ?= never

# ── Container names ─────────────────────────────────────
LLM_CONTAINER ?= vllm-gpt-oss-20b
OPEN_WEBUI_CONTAINER ?= open-webui
CONTAINERS := $(LLM_CONTAINER) $(OPEN_WEBUI_CONTAINER)

# ── GPU pinning ─────────────────────────────────────────
# GPU=0, GPU=1, or GPU=all
GPU ?= 1

ifeq ($(GPU),all)
  GPU_FLAG := --gpus all
else
  GPU_FLAG := --gpus '"device=$(GPU)"'
endif

# ── vLLM defaults ───────────────────────────────────────
# Overridden by the run-gpt-* profile targets below.
GPU_MEM_UTIL ?= 0.92
MAX_MODEL_LEN ?= 16384
TP_SIZE ?= 1
EXTRA_ARGS ?= --max-num-seqs 4 --max-num-batched-tokens 8192
SERVED_MODEL_NAME ?= gpt-oss-20b

# ── Open WebUI settings ─────────────────────────────────
OPEN_WEBUI_HOST_ADDR := $(LLM_CONTAINER)
OPEN_WEBUI_VLLM_BASE_URL := http://$(OPEN_WEBUI_HOST_ADDR):$(PORT)/v1
OPEN_WEBUI_VLLM_API_KEY := local

.PHONY: setup build \
	run-llm run-gpt-balanced run-gpt-32k run-gpt-64k-kvfp8 \
	run-openwebui run-openwebui-with-llm open-openwebui \
	stop stop-all stop-llm stop-openwebui \
	logs logs-llm logs-openwebui \
	healthcheck healthcheck-llm healthcheck-openwebui \
	models shell clean clean-all \
	smoke-single smoke-batch smoke \
	corpus-eval

# ── Setup / build ───────────────────────────────────────
setup:
	mkdir -p $(CACHE_PATH) $(OPEN_WEBUI_DATA)

build:
ifeq ($(DOCKER_PULL_POLICY),never)
	@echo "Skipping docker pull because DOCKER_PULL_POLICY=never"
else
	docker pull $(IMAGE)
endif

# ── Run: vLLM server ────────────────────────────────────
run-llm: stop-llm build setup
	docker network inspect $(DOCKER_NETWORK) >/dev/null 2>&1 || docker network create $(DOCKER_NETWORK)
	docker run -d \
		--name $(LLM_CONTAINER) \
		--network $(DOCKER_NETWORK) \
		$(GPU_FLAG) \
		--ipc=host \
		-p $(PORT):8000 \
		-v $(MODEL_PATH):/model:ro \
		-v $(CACHE_PATH):/root/.cache \
		--restart $(DOCKER_RESTART_POLICY) \
		--pull $(DOCKER_PULL_POLICY) \
		$(IMAGE) \
		/model \
		--served-model-name $(SERVED_MODEL_NAME) \
		--host 0.0.0.0 \
		--port 8000 \
		--gpu-memory-utilization $(GPU_MEM_UTIL) \
		--max-model-len $(MAX_MODEL_LEN) \
		--tensor-parallel-size $(TP_SIZE) \
		--enable-prefix-caching \
		$(EXTRA_ARGS)
	docker logs -f $(LLM_CONTAINER)

# ── GPT profiles ────────────────────────────────────────
# Good everyday single-3090 default
run-gpt-balanced:
	$(MAKE) run-llm \
		GPU=1 \
		TP_SIZE=1 \
		GPU_MEM_UTIL=0.92 \
		MAX_MODEL_LEN=16384 \
		EXTRA_ARGS="--max-num-seqs 4 --max-num-batched-tokens 4096"

run-gpt-32k:
	$(MAKE) run-llm \
		GPU=1 \
		TP_SIZE=1 \
		GPU_MEM_UTIL=0.92 \
		MAX_MODEL_LEN=32768 \
		EXTRA_ARGS="--max-num-seqs 2 --max-num-batched-tokens 4096"

run-gpt-dual-fastest:
	$(MAKE) run-llm \
		GPU=all \
		TP_SIZE=2 \
		GPU_MEM_UTIL=0.88 \
		MAX_MODEL_LEN=16384 \
		EXTRA_ARGS="--max-num-seqs 4 --max-num-batched-tokens 4096"

run-gpt-dual-fast:
	$(MAKE) run-llm \
		GPU=all \
		TP_SIZE=2 \
		GPU_MEM_UTIL=0.88 \
		MAX_MODEL_LEN=16384 \
		EXTRA_ARGS="--max-num-seqs 6 --max-num-batched-tokens 4096"

run-gpt-dual-long:
	$(MAKE) run-llm \
		GPU=all \
		TP_SIZE=2 \
		GPU_MEM_UTIL=0.92 \
		MAX_MODEL_LEN=65536 \
		EXTRA_ARGS="--max-num-seqs 1 --max-num-batched-tokens 8192"

run-gpt-dual-longest:
	$(MAKE) run-llm \
		GPU=all \
		TP_SIZE=2 \
		GPU_MEM_UTIL=0.94 \
		MAX_MODEL_LEN=98304 \
		EXTRA_ARGS="--max-num-seqs 1 --max-num-batched-tokens 8192"

# Experimental 64k profile on one 3090 with FP8 KV cache fp8_e5m2 unsupported on vLLM 0.17.1
# run-gpt-64k-e5m2:
# 	$(MAKE) run-llm \
# 		GPU=1 \
# 		TP_SIZE=1 \
# 		GPU_MEM_UTIL=0.94 \
# 		MAX_MODEL_LEN=65536 \
# 		EXTRA_ARGS="--max-num-seqs 1 --max-num-batched-tokens 8192 --kv-cache-dtype fp8_e5m2 --calculate-kv-scales"



# ── Run: Open WebUI ─────────────────────────────────────
run-openwebui: stop-openwebui setup
	docker network inspect $(DOCKER_NETWORK) >/dev/null 2>&1 || docker network create $(DOCKER_NETWORK)
	docker run -d \
		--name $(OPEN_WEBUI_CONTAINER) \
		--network $(DOCKER_NETWORK) \
		-p $(OPEN_WEBUI_PORT):8080 \
		-v $(OPEN_WEBUI_DATA):/app/backend/data \
		--restart $(DOCKER_RESTART_POLICY) \
		--pull $(DOCKER_PULL_POLICY) \
		-e WEBUI_AUTH=false \
		-e OPENAI_API_BASE_URL=$(OPEN_WEBUI_VLLM_BASE_URL) \
		-e OPENAI_API_BASE_URLS=$(OPEN_WEBUI_VLLM_BASE_URL) \
		-e OPENAI_API_KEY=$(OPEN_WEBUI_VLLM_API_KEY) \
		-e OPENAI_API_KEYS=$(OPEN_WEBUI_VLLM_API_KEY) \
		$(OPEN_WEBUI_IMAGE)
	docker logs -f $(OPEN_WEBUI_CONTAINER)

run-openwebui-with-llm:
	$(MAKE) run-gpt-balanced >/tmp/run-llm.log 2>&1 & \
	sleep 3
	$(MAKE) run-openwebui

open-openwebui:
	@nohup chromium --new-tab http://localhost:$(OPEN_WEBUI_PORT) >/tmp/openwebui.log 2>&1 &

# ── Stop ────────────────────────────────────────────────
stop: stop-llm

stop-all:
	@for c in $(CONTAINERS); do \
		docker stop $$c 2>/dev/null || true; \
		docker rm $$c 2>/dev/null || true; \
	done
	@echo "All containers stopped"

stop-llm:
	docker stop $(LLM_CONTAINER) 2>/dev/null || true
	docker rm $(LLM_CONTAINER) 2>/dev/null || true

stop-openwebui:
	docker stop $(OPEN_WEBUI_CONTAINER) 2>/dev/null || true
	docker rm $(OPEN_WEBUI_CONTAINER) 2>/dev/null || true

# ── Logs ────────────────────────────────────────────────
logs:
	@for c in $(CONTAINERS); do \
		if docker ps --format '{{.Names}}' | grep -q "^$$c$$"; then \
			docker logs -f $$c; \
			exit 0; \
		fi; \
	done; \
	echo "No running container found"

logs-llm:
	docker logs -f $(LLM_CONTAINER)

logs-openwebui:
	docker logs -f $(OPEN_WEBUI_CONTAINER)

# ── Healthchecks ────────────────────────────────────────
healthcheck: healthcheck-llm

healthcheck-llm:
	@curl -sf http://localhost:$(PORT)/health && echo "healthy" || echo "unhealthy"

healthcheck-openwebui:
	@curl -sf http://localhost:$(OPEN_WEBUI_PORT)/health && echo "healthy" || echo "unhealthy"

# ── Utility ─────────────────────────────────────────────
models:
	@curl -s http://localhost:$(PORT)/v1/models | python3 -m json.tool

SMOKE_ENDPOINT ?= http://localhost:8000/v1/responses
SMOKE_OUT ?= .respkit_smoke
SMOKE_INPUT_DIR ?= tests/fixtures/rename_inputs
SMOKE_INPUT_FILE ?= $(SMOKE_INPUT_DIR)/clean_easy.txt
CORPUS_DIR ?= tests/fixtures/rename_inputs
CORPUS_FORMAT ?= csv
CORPUS_EXPORT ?=

smoke-single:
	@rm -rf $(SMOKE_OUT)
	@mkdir -p $(SMOKE_OUT)
	@python3 -m examples.run_rename_proposal single $(SMOKE_INPUT_FILE) --endpoint $(SMOKE_ENDPOINT) --out $(SMOKE_OUT)

smoke-batch:
	@rm -rf $(SMOKE_OUT)
	@mkdir -p $(SMOKE_OUT)
	@python3 -m examples.run_rename_proposal batch $(SMOKE_INPUT_DIR) --endpoint $(SMOKE_ENDPOINT) --out $(SMOKE_OUT)

smoke:
	@$(MAKE) smoke-single
	@$(MAKE) smoke-batch

corpus-eval:
	@python3 scripts/evaluate_corpus.py $(CORPUS_DIR) --endpoint $(SMOKE_ENDPOINT) --out $(SMOKE_OUT) --format $(CORPUS_FORMAT) $(if $(CORPUS_EXPORT),--export $(CORPUS_EXPORT),)

shell:
	@for c in $(CONTAINERS); do \
		if docker ps --format '{{.Names}}' | grep -q "^$$c$$"; then \
			docker exec -it $$c /bin/bash; \
			exit 0; \
		fi; \
	done; \
	echo "No running container found"

# ── Clean ───────────────────────────────────────────────
clean: stop-llm
	docker rmi $(IMAGE) 2>/dev/null || true

clean-all: stop-all
	docker rmi $(IMAGE) 2>/dev/null || true
