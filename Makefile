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

.PHONY: deps deps-test lint test test-components build eval eval-init \
	docker-build docker-build-api docker-build-embed docker-build-normalize docker-build-all \
	docker-push docker-push-api docker-push-embed docker-push-normalize docker-push-all \
	docker-run-worker docker-run-normalize docker-run-normalize-watch \
	local-dev-config local-dev-up local-dev-down local-dev-logs local-dev-model

# -----------------------------------------------------------------------------
# Python environment
# -----------------------------------------------------------------------------
$(VENV_PYTHON):
	$(PYTHON) -m venv $(VENV_DIR)

deps:
	$(VENV_PYTHON) -m pip install --upgrade pip
	$(VENV_PYTHON) -m pip install -r requirements.txt

deps-test:
	$(VENV_PYTHON) -m pip install --upgrade pip
	$(VENV_PYTHON) -m pip install -r requirements-test.txt

# -----------------------------------------------------------------------------
# Quality checks and tests
# -----------------------------------------------------------------------------
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

# -----------------------------------------------------------------------------
# Agent evaluations
# -----------------------------------------------------------------------------
eval: $(VENV_PYTHON)
	$(VENV_PYTHON) -m app.agent.evals evals/cases.local.json

eval-init:
	@test ! -e evals/cases.local.json || (echo "evals/cases.local.json already exists" >&2; exit 1)
	cp evals/cases.example.json evals/cases.local.json

# -----------------------------------------------------------------------------
# Docker image build and publish
# -----------------------------------------------------------------------------
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

# -----------------------------------------------------------------------------
# Docker worker commands
# -----------------------------------------------------------------------------
docker-run-worker:
	docker run --rm $(EMBED_IMAGE):$(TAG) python -m app.embed.worker

docker-run-normalize:
	docker run --rm $(NORMALIZE_IMAGE):$(TAG) python -m app.normalize.worker

docker-run-normalize-watch:
	docker run --rm $(NORMALIZE_IMAGE):$(TAG) python -m app.normalize.main

# -----------------------------------------------------------------------------
# Local development stack
# -----------------------------------------------------------------------------
# llama.cpp runs natively on the Mac so the model can use Apple Metal acceleration.
local-dev-config:
	@mkdir -p local-dev/config local-dev/data/postgres
	@test -f local-dev/config/runtime-settings.json || cp local-dev/config/runtime-settings.example.json local-dev/config/runtime-settings.json

local-dev-up: local-dev-config
	docker compose -p rag-local-dev -f docker-compose.yml -f local-dev/compose.yml up --build -d

local-dev-down:
	docker compose -p rag-local-dev -f docker-compose.yml -f local-dev/compose.yml down

local-dev-logs:
	docker compose -p rag-local-dev -f docker-compose.yml -f local-dev/compose.yml logs -f api db

local-dev-model:
	@command -v llama-server >/dev/null || (echo "Install llama.cpp first: brew install llama.cpp" >&2; exit 1)
	llama-server -hf Qwen/Qwen2.5-1.5B-Instruct-GGUF:Q4_K_M --host 0.0.0.0 --port 8080 --ctx-size 8192 --parallel 1 --api-key local-chat-dev-token
