SHELL := /bin/zsh

WORKER_DIR := infra/service-bridge-workers
PYTHON := .venv/bin/python
PYTEST := .venv/bin/pytest

.PHONY: install check python-test worker-install worker-check worker-test worker-build terraform-check

install:
	uv sync --system-certs --extra test
	npm --prefix $(WORKER_DIR) ci

check: python-test worker-check worker-test worker-build
	uv lock --check
	git diff --check

python-test:
	$(PYTEST) -q

worker-install:
	npm --prefix $(WORKER_DIR) ci

worker-check:
	npm --prefix $(WORKER_DIR) run check

worker-test:
	npm --prefix $(WORKER_DIR) test -- --run

worker-build:
	npm --prefix $(WORKER_DIR) run build

terraform-check:
	terraform -chdir=$(WORKER_DIR)/terraform fmt -check
	terraform -chdir=$(WORKER_DIR)/terraform init -backend=false
	terraform -chdir=$(WORKER_DIR)/terraform validate
