from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parents[1]


def test_project_mcp_definition_uses_cloud_safe_launcher():
    config = tomllib.loads((ROOT / ".codex/config.toml").read_text())
    server = config["mcp_servers"]["workers-bridge-cloud"]
    assert server["command"] == "/bin/bash"
    assert "git rev-parse --show-toplevel" in server["args"][1]
    assert "git fetch origin main" not in server["args"][1]
    assert "git merge --ff-only origin/main" not in server["args"][1]
    assert "bash \"$root/.codex/setup.sh\"" not in server["args"][1]
    assert '"$PWD"' in server["args"][1]
    assert '"$PWD/mcp-workers-bridge"' in server["args"][1]
    assert 'exec "$candidate/.venv/bin/mcp-workers-bridge"' in server["args"][1]
    assert server["startup_timeout_sec"] == 30
    assert server["tool_timeout_sec"] == 300


def test_project_mcp_forwards_required_cloud_environment():
    config = tomllib.loads((ROOT / ".codex/config.toml").read_text())
    forwarded = set(
        config["mcp_servers"]["workers-bridge-cloud"]["env_vars"]
    )
    assert {
        "SERVICE_BRIDGE_WORKER_URL",
        "CF_ACCESS_CLIENT_ID",
        "CF_ACCESS_CLIENT_SECRET",
        "YOUTUBE_STORAGE_STATE_JSON",
        "LOCAL_CACHE_ROOT",
        "S3_CACHE_BUCKET",
        "AWS_REGION",
        "AWS_PROFILE",
        "AWS_CONFIG_FILE",
        "AWS_SHARED_CREDENTIALS_FILE",
        "HTTPS_PROXY",
        "SSL_CERT_FILE",
    } <= forwarded


def test_cloud_setup_installs_both_repository_components():
    setup = (ROOT / ".codex/setup.sh").read_text()
    assert "uv sync --system-certs --extra test" in setup
    assert "npm --prefix infra/service-bridge-workers ci" in setup
    assert "test -x .venv/bin/mcp-workers-bridge" in setup
    assert 'export UV_CACHE_DIR="$setup_cache/uv"' in setup
    assert 'export UV_PYTHON_INSTALL_DIR="$setup_cache/python"' in setup
    assert 'export npm_config_cache="$setup_cache/npm"' in setup
    assert 'export XDG_CONFIG_HOME="$setup_cache/xdg-config"' in setup
    assert 'export WRANGLER_LOG_PATH="$setup_cache/wrangler/logs"' in setup
    assert "mktemp -d /tmp/mcp-workers-bridge-setup" in setup
    assert 'CODEX_HOME' in setup
    assert "configure_trust.py" not in setup
    assert 'trust_level = "trusted"' in setup
    assert 'injected by .codex/setup.sh' in setup
    assert 'MCP_LAUNCHER_LINE="$launcher_line" awk' in setup
    assert 'injected="${trust_section}"$' in setup
    assert '[[ "$user_config_content" == *"$injected"* ]]' in setup
    assert '${user_config_content%%"$marker"*}' in setup
    assert 'mv "$tmp_config" "$user_config"' in setup
    assert "printf '%s\\n' \"$repo_config\"" in setup


def test_make_targets_use_writable_caches():
    makefile = (ROOT / "Makefile").read_text()
    assert "BRIDGE_CACHE := $(CURDIR)/.cache/workers" in makefile
    assert 'npm_config_cache="$${npm_config_cache:-$(BRIDGE_CACHE)/npm}"' in makefile
    assert (
        'XDG_CONFIG_HOME="$${XDG_CONFIG_HOME:-$(BRIDGE_CACHE)/xdg-config}"'
        in makefile
    )
    assert (
        'WRANGLER_LOG_PATH="$${WRANGLER_LOG_PATH:-$(BRIDGE_CACHE)/wrangler/logs}"'
        in makefile
    )
    assert (
        'UV_TOOL_DIR="$${UV_TOOL_DIR:-$(BRIDGE_CACHE)/tools}" \\\n'
        "\tuv lock --check"
        in makefile
    )
    assert ".cache/" in (ROOT / ".gitignore").read_text()
