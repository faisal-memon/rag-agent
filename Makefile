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

.PHONY: deps deps-test lint test build docker-build docker-build-api docker-build-embed docker-build-normalize docker-build-all docker-push docker-push-api docker-push-embed docker-push-normalize docker-push-all docker-run-worker docker-run-normalize docker-run-normalize-watch

$(VENV_PYTHON):
	$(PYTHON) -m venv $(VENV_DIR)

deps:
	$(VENV_PYTHON) -m pip install --upgrade pip
	$(VENV_PYTHON) -m pip install -r requirements.txt

deps-test:
	$(VENV_PYTHON) -m pip install --upgrade pip
	$(VENV_PYTHON) -m pip install -r requirements-test.txt

lint: $(VENV_PYTHON)
	$(VENV_PYTHON) -m compileall app

test: $(VENV_PYTHON) deps-test
	$(VENV_PYTHON) -m compileall app
	$(VENV_PYTHON) -m unittest discover -s tests -v

build: $(VENV_PYTHON)
	$(VENV_PYTHON) -m compileall app

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
