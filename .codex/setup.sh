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
# checkout in its persistent user-level Codex configuration. Setup injects both
# a trust entry for this checkout and the repository configuration under one
# marker. On every run, everything from the marker to the end of the file is
# replaced, so updates overwrite the previous block instead of duplicating it.
# Content before the marker is never modified.
user_config="$codex_home/config.toml"
marker='# mcp-workers-bridge (injected by .codex/setup.sh)'
quoted_root="${root//\\/\\\\}"
quoted_root="${quoted_root//\"/\\\"}"
trust_section="$(printf '[projects."%s"]\ntrust_level = "trusted"\n' "$quoted_root")"
repo_config="$(cat "$root/.codex/config.toml")"
injected="${trust_section}"$'\n'"${repo_config}"
user_config_content="$(cat "$user_config" 2>/dev/null || true)"

if [[ "$user_config_content" == *"$injected"* ]]; then
  :
elif [[ "$user_config_content" == *"$marker"* ]]; then
  tmp_config="$user_config.tmp"
  {
    printf '%s' "${user_config_content%%"$marker"*}"
    printf '%s\n\n' "$marker"
    printf '%s' "$trust_section"
    printf '\n'
    cat "$root/.codex/config.toml"
  } > "$tmp_config"
  chmod 600 "$tmp_config"
  mv "$tmp_config" "$user_config"
else
  {
    printf '\n%s\n\n' "$marker"
    printf '%s' "$trust_section"
    printf '\n'
    cat "$root/.codex/config.toml"
  } >> "$user_config"
fi
