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
        "workers_x_bookmarks",
        "workers_x_graphql",
        "workers_x_home",
        "workers_x_likes",
        "workers_youtube_captions",
        "workers_youtube_history",
    }


def test_bridge_configuration_does_not_reveal_credentials(monkeypatch):
    monkeypatch.setenv("SERVICE_BRIDGE_WORKER_URL", "https://bridge.example.org")
    monkeypatch.setenv("CF_ACCESS_CLIENT_ID", "synthetic-id")
    monkeypatch.setenv("CF_ACCESS_CLIENT_SECRET", "synthetic-secret-value")
    result = server.bridge_configuration()
    serialized = str(result)
    assert result["workers"]["configured"] is True
    assert "legacy" not in result
    assert "synthetic-id" not in serialized
    assert "synthetic-secret-value" not in serialized


def test_youtube_history_rejects_bad_page_limit_before_loading_secrets(monkeypatch):
    for name in (
        "SERVICE_BRIDGE_WORKER_URL",
        "WORKERS_BRIDGE_URL",
        "CF_ACCESS_CLIENT_ID",
        "CF_ACCESS_CLIENT_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)
    assert server.workers_youtube_history(max_pages=0) == {
        "ok": False,
        "error": "invalid_argument",
        "message": "max_pages must be between 1 and 500.",
    }


def test_youtube_history_cache_only_does_not_require_worker_config(monkeypatch):
    captured = {}

    def history(config, since, max_pages, timezone, **kwargs):
        captured.update(
            config=config,
            since=since,
            max_pages=max_pages,
            timezone=timezone,
            **kwargs,
        )
        return {"ok": True}

    monkeypatch.setattr(server, "youtube_history", history)
    result = server.workers_youtube_history(
        since="2026-11-01",
        refresh=False,
    )
    assert result == {"ok": True}
    assert captured == {
        "config": None,
        "since": "2026-11-01",
        "max_pages": 100,
        "timezone": None,
        "refresh": False,
        "cache_enabled": True,
    }
