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

# Merge the project MCP table into the user-level Codex configuration so the
# server is discoverable outside the trusted-project layer. The injected part
# starts with the marker below; on later runs everything from the marker to the
# end of the file is replaced with the current repository configuration, so an
# updated repository configuration overwrites the previous block instead of
# duplicating it. Content before the marker is never modified.
user_config="$codex_home/config.toml"
repo_config="$(cat "$root/.codex/config.toml")"
marker='# mcp-workers-bridge (injected by .codex/setup.sh)'
user_config_content="$(cat "$user_config" 2>/dev/null || true)"

if [[ "$user_config_content" == *"$marker"* ]]; then
  tmp_config="$user_config.tmp"
  {
    printf '%s' "${user_config_content%%"$marker"*}"
    printf '%s\n\n' "$marker"
    cat "$root/.codex/config.toml"
  } > "$tmp_config"
  chmod 600 "$tmp_config"
  mv "$tmp_config" "$user_config"
elif [[ "$user_config_content" != *"$repo_config"* ]]; then
  {
    printf '\n%s\n\n' "$marker"
    cat "$root/.codex/config.toml"
  } >> "$user_config"
fi
