import pytest

from mcp_workers_bridge.config import WorkersConfig, worker_origin
from mcp_workers_bridge.errors import ConfigurationError


def test_worker_config_uses_existing_environment_names(monkeypatch):
    monkeypatch.setenv("SERVICE_BRIDGE_WORKER_URL", "https://bridge.example.org/")
    monkeypatch.setenv("CF_ACCESS_CLIENT_ID", "synthetic-id")
    monkeypatch.setenv("CF_ACCESS_CLIENT_SECRET", "synthetic-secret")
    config = WorkersConfig.from_env()
    assert config.base_url == "https://bridge.example.org"
    assert config.client_id == "synthetic-id"
    assert config.client_secret == "synthetic-secret"


@pytest.mark.parametrize(
    "url",
    [
        "http://bridge.example.org",
        "https://localhost",
        "https://127.0.0.1",
        "https://user@bridge.example.org",
        "https://bridge.example.org:444",
        "https://bridge.example.org/path",
        "https://bridge.example.org/?query",
        "https://bridge.example.org/#fragment",
    ],
)
def test_worker_url_rejects_ambiguous_or_unsafe_values(url):
    with pytest.raises(ConfigurationError):
        worker_origin(url)
