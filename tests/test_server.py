import asyncio

import pytest

from mcp_workers_bridge import server


def test_server_exposes_expected_tools():
    tools = asyncio.run(server.mcp.list_tools())
    assert {tool.name for tool in tools} == {
        "bridge_configuration",
        "workers_bridge_probe",
        "workers_reddit_comments",
        "workers_reddit_identity",
        "workers_reddit_listing",
        "workers_x_asset",
        "workers_x_graphql",
        "workers_x_home",
        "legacy_bridge_probe",
        "workers_youtube_captions",
    }


def test_bridge_configuration_does_not_reveal_credentials(monkeypatch):
    monkeypatch.setenv("SERVICE_BRIDGE_WORKER_URL", "https://bridge.example.org")
    monkeypatch.setenv("CF_ACCESS_CLIENT_ID", "synthetic-id")
    monkeypatch.setenv("CF_ACCESS_CLIENT_SECRET", "synthetic-secret-value")
    result = server.bridge_configuration()
    serialized = str(result)
    assert result["workers"]["configured"] is True
    assert "synthetic-id" not in serialized
    assert "synthetic-secret-value" not in serialized
