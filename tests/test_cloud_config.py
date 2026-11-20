from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parents[1]


def test_project_mcp_definition_uses_cloud_safe_launcher():
    config = tomllib.loads((ROOT / ".codex/config.toml").read_text())
    server = config["mcp_servers"]["workers-bridge-cloud"]
    assert server["command"] == "/bin/bash"
    assert "git rev-parse --show-toplevel" in server["args"][1]
    assert server["args"][1].endswith("exec .venv/bin/mcp-workers-bridge")
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
        "YOUTUBE_CACHE_DIR",
        "YOUTUBE_CACHE_BUCKET",
        "AWS_REGION",
        "HTTPS_PROXY",
        "SSL_CERT_FILE",
    } <= forwarded


def test_cloud_setup_installs_both_repository_components():
    setup = (ROOT / ".codex/setup.sh").read_text()
    assert "uv sync --system-certs --extra test" in setup
    assert "npm --prefix infra/service-bridge-workers ci" in setup
    assert "test -x .venv/bin/mcp-workers-bridge" in setup
    assert 'trust_level = "trusted"' in setup
    assert 'CODEX_HOME' in setup
