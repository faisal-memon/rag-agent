PYTHON ?= python3
VENV_DIR ?= .venv
VENV_PYTHON := $(VENV_DIR)/bin/python
GHCR_OWNER ?= your-github-user-or-org
IMAGE ?= ghcr.io/$(GHCR_OWNER)/rag
API_IMAGE ?= ghcr.io/$(GHCR_OWNER)/rag-api
EMBED_IMAGE ?= ghcr.io/$(GHCR_OWNER)/rag-embed
NORMALIZE_IMAGE ?= ghcr.io/$(GHCR_OWNER)/rag-normalize
TAG ?= latest
DOCKER_TARGET ?= api

.PHONY: deps deps-test lint test test-components build eval eval-init docker-build docker-build-api docker-build-embed docker-build-normalize docker-build-all docker-push docker-push-api docker-push-embed docker-push-normalize docker-push-all docker-run-worker docker-run-normalize docker-run-normalize-watch local-chat-config local-chat-up local-chat-down local-chat-logs local-chat-model

$(VENV_PYTHON):
	$(PYTHON) -m venv $(VENV_DIR)

deps:
	$(VENV_PYTHON) -m pip install --upgrade pip
	$(VENV_PYTHON) -m pip install -r requirements.txt

deps-test:
	$(VENV_PYTHON) -m pip install --upgrade pip
	$(VENV_PYTHON) -m pip install -r requirements-test.txt

lint: $(VENV_PYTHON)
	$(VENV_PYTHON) -m compileall app scripts

test: $(VENV_PYTHON) deps-test
	$(VENV_PYTHON) -m unittest discover -s tests -v

test-components: $(VENV_PYTHON)
	@set -e; \
	for component in $(COMPONENTS); do \
		$(VENV_PYTHON) -m pip install -r "requirements-$$component.txt"; \
	done
	$(VENV_PYTHON) scripts/run_component_tests.py $(COMPONENTS)

build: $(VENV_PYTHON)
	$(VENV_PYTHON) -m compileall app

eval: $(VENV_PYTHON)
	$(VENV_PYTHON) -m app.agent.evals evals/cases.local.json

eval-init:
	@test ! -e evals/cases.local.json || (echo "evals/cases.local.json already exists" >&2; exit 1)
	cp evals/cases.example.json evals/cases.local.json

docker-build:
	docker build --target $(DOCKER_TARGET) -t $(IMAGE):$(TAG) .

docker-build-api:
	$(MAKE) docker-build DOCKER_TARGET=api IMAGE=$(API_IMAGE)

docker-build-embed:
	$(MAKE) docker-build DOCKER_TARGET=embed IMAGE=$(EMBED_IMAGE)

docker-build-normalize:
	$(MAKE) docker-build DOCKER_TARGET=normalize IMAGE=$(NORMALIZE_IMAGE)

docker-build-all: docker-build-api docker-build-embed docker-build-normalize

docker-push:
	docker build --target $(DOCKER_TARGET) -t $(IMAGE):$(TAG) .
	docker push $(IMAGE):$(TAG)

docker-push-api:
	$(MAKE) docker-push DOCKER_TARGET=api IMAGE=$(API_IMAGE)

docker-push-embed:
	$(MAKE) docker-push DOCKER_TARGET=embed IMAGE=$(EMBED_IMAGE)

docker-push-normalize:
	$(MAKE) docker-push DOCKER_TARGET=normalize IMAGE=$(NORMALIZE_IMAGE)

docker-push-all: docker-push-api docker-push-embed docker-push-normalize

docker-run-worker:
	docker run --rm $(EMBED_IMAGE):$(TAG) python -m app.embed.worker

docker-run-normalize:
	docker run --rm $(NORMALIZE_IMAGE):$(TAG) python -m app.normalize.worker

docker-run-normalize-watch:
	docker run --rm $(NORMALIZE_IMAGE):$(TAG) python -m app.normalize.main

# Phase 1: local chat UI + PostgreSQL in Compose, with llama.cpp running natively
# on the Mac so Apple Metal acceleration is available.
local-chat-config:
	@mkdir -p local-chat-config local-chat-data/postgres
	@test -f local-chat-config/runtime-settings.json || cp config/runtime-settings.local-chat.example.json local-chat-config/runtime-settings.json

local-chat-up: local-chat-config
	docker compose -f docker-compose.local-chat.yml up --build -d

local-chat-down:
	docker compose -f docker-compose.local-chat.yml down

local-chat-logs:
	docker compose -f docker-compose.local-chat.yml logs -f api db

local-chat-model:
	@command -v llama-server >/dev/null || (echo "Install llama.cpp first: brew install llama.cpp" >&2; exit 1)
	llama-server -hf Qwen/Qwen2.5-1.5B-Instruct-GGUF:Q4_K_M --host 0.0.0.0 --port 8080 --ctx-size 8192 --parallel 1 --api-key local-chat-dev-token
