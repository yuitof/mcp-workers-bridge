#!/usr/bin/env bash
set -euo pipefail

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

if [[ -n "${MCP_WORKERS_BRIDGE_SETUP_CACHE:-}" ]]; then
  setup_cache="$MCP_WORKERS_BRIDGE_SETUP_CACHE"
  mkdir -p "$setup_cache"
else
  setup_cache="$(mktemp -d /tmp/mcp-workers-bridge-setup.XXXXXX)"
fi

# Cloud tasks may mount the home-directory cache read-only. Keep package-manager
# state with the writable task filesystem instead of relying on $HOME.
export UV_CACHE_DIR="$setup_cache/uv"
export UV_PYTHON_INSTALL_DIR="$setup_cache/python"
export UV_TOOL_DIR="$setup_cache/tools"
export npm_config_cache="$setup_cache/npm"
export XDG_CACHE_HOME="$setup_cache/xdg"
mkdir -p \
  "$UV_CACHE_DIR" \
  "$UV_PYTHON_INSTALL_DIR" \
  "$UV_TOOL_DIR" \
  "$npm_config_cache" \
  "$XDG_CACHE_HOME"

uv sync --system-certs --extra test
npm --prefix infra/service-bridge-workers ci

test -x .venv/bin/mcp-workers-bridge

codex_home="${CODEX_HOME:-$HOME/.codex}"
mkdir -p "$codex_home"

# Project-scoped MCP configuration is ignored unless the Cloud task trusts the
# checkout in its persistent user-level Codex configuration.
.venv/bin/python "$root/.codex/configure_trust.py" \
  "$codex_home/config.toml" "$root"
