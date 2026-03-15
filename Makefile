# Local GPT-OSS 20B via vLLM + optional Open WebUI
# Default target profile is tuned for a single RTX 3090 24 GB.

# ── Images ──────────────────────────────────────────────
IMAGE_NAME := vllm/vllm-openai
IMAGE_TAG  := latest
IMAGE      := $(IMAGE_NAME):$(IMAGE_TAG)

OPEN_WEBUI_IMAGE := ghcr.io/open-webui/open-webui:main

# ── Model / paths ───────────────────────────────────────
MODEL_PATH ?= /data/models/openai/gpt-oss-20b
CACHE_PATH := $(HOME)/.cache/vllm

# ── Networking / ports ──────────────────────────────────
PORT              ?= 8000
OPEN_WEBUI_PORT   ?= 3002
DOCKER_NETWORK    ?= local-inference-net

# ── Container names ─────────────────────────────────────
LLM_CONTAINER          ?= vllm-gpt-oss-20b
OPEN_WEBUI_CONTAINER   ?= open-webui
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
# These are overridden by the run-gpt-* profile targets below.
GPU_MEM_UTIL ?= 0.88
MAX_MODEL_LEN ?= 8192
TP_SIZE ?= 1
EXTRA_ARGS ?= --max-num-seqs 4

# ── Open WebUI settings ─────────────────────────────────
OPEN_WEBUI_HOST_ADDR      := $(LLM_CONTAINER)
OPEN_WEBUI_VLLM_BASE_URL  := http://$(OPEN_WEBUI_HOST_ADDR):$(PORT)/v1
OPEN_WEBUI_VLLM_API_KEY   := local
OPEN_WEBUI_DATA           := $(HOME)/.local/share/open-webui

.PHONY: setup build \
	run-llm run-gpt-safe run-gpt-balanced run-gpt-dual \
	run-openwebui run-openwebui-with-llm open-openwebui \
	stop stop-all stop-llm stop-openwebui \
	logs logs-llm logs-openwebui \
	healthcheck healthcheck-llm healthcheck-openwebui \
	models shell clean clean-all

# ── Setup / build ───────────────────────────────────────
setup:
	mkdir -p $(CACHE_PATH) $(OPEN_WEBUI_DATA)

build:
	docker pull $(IMAGE)

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
		-v $(CACHE_PATH):/root/.cache/huggingface \
		--restart unless-stopped \
		$(IMAGE) \
		--model /model \
		--host 0.0.0.0 \
		--port 8000 \
		--gpu-memory-utilization $(GPU_MEM_UTIL) \
		--max-model-len $(MAX_MODEL_LEN) \
		--tensor-parallel-size $(TP_SIZE) \
		--enable-prefix-caching \
		$(EXTRA_ARGS)
	docker logs -f $(LLM_CONTAINER)

# ── GPT profiles ────────────────────────────────────────
# Conservative single-3090 profile
run-gpt-safe:
	$(MAKE) run-llm \
		GPU=1 \
		TP_SIZE=1 \
		GPU_MEM_UTIL=0.88 \
		MAX_MODEL_LEN=4096 \
		EXTRA_ARGS="--max-num-seqs 2 --max-num-batched-tokens 4096"

# Good everyday single-3090 default
run-gpt-balanced:
	$(MAKE) run-llm \
		GPU=1 \
		TP_SIZE=1 \
		GPU_MEM_UTIL=0.92 \
		MAX_MODEL_LEN=16384 \
		EXTRA_ARGS="--max-num-seqs 4 --max-num-batched-tokens 8192"

# Long-context single-3090 profile
run-gpt-32k:
	$(MAKE) run-llm \
		GPU=1 \
		TP_SIZE=1 \
		GPU_MEM_UTIL=0.92 \
		MAX_MODEL_LEN=32768 \
		EXTRA_ARGS="--max-num-seqs 2 --max-num-batched-tokens 8192"

# Experimental 64k profile on one 3090
run-gpt-64k:
	$(MAKE) run-llm \
		GPU=1 \
		TP_SIZE=1 \
		GPU_MEM_UTIL=0.94 \
		MAX_MODEL_LEN=65536 \
		EXTRA_ARGS="--max-num-seqs 1 --max-num-batched-tokens 8192"

# 64k with FP8 KV cache
run-gpt-64k-kvfp8:
	$(MAKE) run-llm \
		GPU=1 \
		TP_SIZE=1 \
		GPU_MEM_UTIL=0.94 \
		MAX_MODEL_LEN=65536 \
		EXTRA_ARGS="--max-num-seqs 1 --max-num-batched-tokens 8192 --kv-cache-dtype fp8 --calculate-kv-scales"

# Optional dual-GPU profile for more headroom / lower latency
run-gpt-dual:
	$(MAKE) run-llm \
		GPU=all \
		TP_SIZE=2 \
		GPU_MEM_UTIL=0.90 \
		MAX_MODEL_LEN=32768 \
		EXTRA_ARGS="--max-num-seqs 6 --max-num-batched-tokens 8192"

# ── Run: Open WebUI ─────────────────────────────────────
run-openwebui: stop-openwebui setup
	docker network inspect $(DOCKER_NETWORK) >/dev/null 2>&1 || docker network create $(DOCKER_NETWORK)
	docker run -d \
		--name $(OPEN_WEBUI_CONTAINER) \
		--network $(DOCKER_NETWORK) \
		-p $(OPEN_WEBUI_PORT):8080 \
		-v $(OPEN_WEBUI_DATA):/app/backend/data \
		--restart unless-stopped \
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