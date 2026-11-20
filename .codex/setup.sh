#!/usr/bin/env bash
set -euo pipefail

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

uv sync --system-certs --extra test
npm --prefix infra/service-bridge-workers ci

test -x .venv/bin/mcp-workers-bridge

codex_home="${CODEX_HOME:-$HOME/.codex}"
mkdir -p "$codex_home"

# Project-scoped MCP configuration is ignored unless the Cloud task trusts the
# checkout in its persistent user-level Codex configuration.
.venv/bin/python "$root/.codex/configure_trust.py" \
  "$codex_home/config.toml" "$root"
