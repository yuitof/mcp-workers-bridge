from pathlib import Path
import stat
import subprocess
import sys
import tomllib


ROOT = Path(__file__).resolve().parents[1]


def test_project_mcp_definition_uses_cloud_safe_launcher():
    config = tomllib.loads((ROOT / ".codex/config.toml").read_text())
    server = config["mcp_servers"]["workers-bridge-cloud"]
    assert server["command"] == "/bin/bash"
    assert "git rev-parse --show-toplevel" in server["args"][1]
    assert "git fetch origin main" in server["args"][1]
    assert "git merge --ff-only origin/main" in server["args"][1]
    assert "bash \"$root/.codex/setup.sh\"" in server["args"][1]
    assert server["args"][1].endswith(
        'exec "$root/.venv/bin/mcp-workers-bridge"'
    )
    assert server["startup_timeout_sec"] == 600
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
    assert "mktemp -d /tmp/mcp-workers-bridge-setup" in setup
    assert 'CODEX_HOME' in setup
    assert "configure_trust.py" in setup
    assert 'injected by .codex/setup.sh' in setup
    assert 'cat "$root/.codex/config.toml"' in setup

    trust = (ROOT / ".codex/configure_trust.py").read_text()
    assert 'trust_level = "trusted"' in trust


def test_cloud_trust_replaces_read_only_config(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text(
        '[model_providers.existing]\nname = "fixture"\n',
        encoding="utf-8",
    )
    config.chmod(0o444)
    project = tmp_path / "project"
    project.mkdir()

    subprocess.run(
        [
            sys.executable,
            str(ROOT / ".codex/configure_trust.py"),
            str(config),
            str(project),
        ],
        check=True,
    )

    parsed = tomllib.loads(config.read_text(encoding="utf-8"))
    assert parsed["model_providers"]["existing"]["name"] == "fixture"
    assert parsed["projects"][str(project)]["trust_level"] == "trusted"
    assert stat.S_IMODE(config.stat().st_mode) == 0o600


def test_cloud_trust_accepts_existing_read_only_trust_entry(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    config = tmp_path / "config.toml"
    config.write_text(
        f'[projects.{project.as_posix()!r}]\n'
        "trust_level = 'trusted'\n"
        'existing_setting = "preserved"\n',
        encoding="utf-8",
    )
    config.chmod(0o444)
    inode = config.stat().st_ino

    subprocess.run(
        [
            sys.executable,
            str(ROOT / ".codex/configure_trust.py"),
            str(config),
            str(project),
        ],
        check=True,
    )

    assert config.stat().st_ino == inode
    parsed = tomllib.loads(config.read_text(encoding="utf-8"))
    assert parsed["projects"][str(project)] == {
        "trust_level": "trusted",
        "existing_setting": "preserved",
    }
