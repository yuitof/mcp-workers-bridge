SHELL := /bin/zsh

WORKER_DIR := infra/service-bridge-workers
BRIDGE_CACHE := $(CURDIR)/.cache/workers
PYTHON := .venv/bin/python
PYTEST := .venv/bin/pytest

.PHONY: install check python-test worker-install worker-check worker-test worker-build terraform-check

install:
	UV_CACHE_DIR="$${UV_CACHE_DIR:-$(BRIDGE_CACHE)/uv}" \
	UV_PYTHON_INSTALL_DIR="$${UV_PYTHON_INSTALL_DIR:-$(BRIDGE_CACHE)/python}" \
	UV_TOOL_DIR="$${UV_TOOL_DIR:-$(BRIDGE_CACHE)/tools}" \
	uv sync --system-certs --extra test
	npm_config_cache="$${npm_config_cache:-$(BRIDGE_CACHE)/npm}" \
	npm --prefix $(WORKER_DIR) ci

check: python-test worker-check worker-test worker-build
	uv lock --check
	git diff --check

python-test:
	$(PYTEST) -q

worker-install:
	npm_config_cache="$${npm_config_cache:-$(BRIDGE_CACHE)/npm}" \
	npm --prefix $(WORKER_DIR) ci

worker-check:
	npm --prefix $(WORKER_DIR) run check

worker-test:
	npm --prefix $(WORKER_DIR) test -- --run

worker-build:
	mkdir -p "$(BRIDGE_CACHE)/xdg-config" "$(BRIDGE_CACHE)/wrangler/logs"
	XDG_CONFIG_HOME="$${XDG_CONFIG_HOME:-$(BRIDGE_CACHE)/xdg-config}" \
	WRANGLER_LOG_PATH="$${WRANGLER_LOG_PATH:-$(BRIDGE_CACHE)/wrangler/logs}" \
	npm --prefix $(WORKER_DIR) run build

terraform-check:
	terraform -chdir=$(WORKER_DIR)/terraform fmt -check
	terraform -chdir=$(WORKER_DIR)/terraform init -backend=false
	terraform -chdir=$(WORKER_DIR)/terraform validate
