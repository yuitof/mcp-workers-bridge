import json
from types import SimpleNamespace

import pytest

from mcp_workers_bridge import social
from mcp_workers_bridge.config import WorkersConfig
from mcp_workers_bridge.errors import ConfigurationError


CONFIG = WorkersConfig(
    base_url="https://bridge.example.org",
    client_id="synthetic-id",
    client_secret="synthetic-secret",
    timeout=30,
)


def state(service, cookie):
    return json.dumps(
        {
            "cookies": [
                {
                    "name": cookie[0],
                    "value": cookie[1],
                    "domain": ".x.com" if service == "x" else ".reddit.com",
                    "path": "/",
                    "expires": -1,
                }
            ]
        }
    )


def test_x_home_adds_private_cookies(monkeypatch):
    monkeypatch.setenv(
        "X_STORAGE_STATE_JSON",
        json.dumps(
            {
                "cookies": [
                    {"name": "auth_token", "value": "synthetic-auth", "domain": ".x.com", "path": "/", "expires": -1},
                    {"name": "ct0", "value": "synthetic-csrf", "domain": ".x.com", "path": "/", "expires": -1},
                ]
            }
        ),
    )
    captured = {}

    class Client:
        def __init__(self, config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def relay(self, service, operation, parameters, **kwargs):
            captured["headers"] = kwargs["headers"]
            return {
                "ok": True,
                "bridge_ok": True,
                "body": "home",
                "_set_cookies": [],
            }

    monkeypatch.setattr(social, "WorkersClient", Client)
    result = social.x_home(CONFIG)
    assert result["ok"] is True
    assert "auth_token=synthetic-auth" in captured["headers"]["Cookie"]
    assert "ct0=synthetic-csrf" in captured["headers"]["Cookie"]


def test_reddit_listing_builds_typed_request(monkeypatch):
    monkeypatch.setenv(
        "REDDIT_STORAGE_STATE_JSON",
        state("reddit", ("reddit_session", "synthetic-session")),
    )
    captured = {}

    class Client:
        def __init__(self, config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def relay(self, service, operation, parameters, **kwargs):
            captured.update(
                service=service,
                operation=operation,
                parameters=parameters,
                headers=kwargs["headers"],
            )
            return {"ok": True, "bridge_ok": True, "body": "{}", "_set_cookies": []}

    monkeypatch.setattr(social, "WorkersClient", Client)
    social.reddit_listing(CONFIG, "synthetic_user", "saved", "t3_abc")
    assert captured["parameters"] == {
        "account": "synthetic_user",
        "collection": "saved",
        "after": "t3_abc",
    }
    assert "reddit_session=synthetic-session" in captured["headers"]["Cookie"]


def test_missing_private_session_is_sanitized(monkeypatch):
    monkeypatch.delenv("REDDIT_STORAGE_STATE_JSON", raising=False)
    monkeypatch.delenv("VAULT_ROOT", raising=False)
    with pytest.raises(ConfigurationError, match="REDDIT_STORAGE_STATE_JSON"):
        social.CookieState("reddit")


def test_x_asset_never_returns_origin_cookies(monkeypatch):
    class Client:
        def __init__(self, config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def relay(self, service, operation, parameters, **kwargs):
            return {
                "ok": True,
                "bridge_ok": True,
                "body": "javascript",
                "_set_cookies": ["auth_token=must-not-escape"],
            }

    monkeypatch.setattr(social, "WorkersClient", Client)
    result = social.x_asset(CONFIG, "main.synthetic.js")
    assert "auth_token=must-not-escape" not in str(result)


def test_direct_target_maps_destinations():
    assert social.direct_target("x", "home", {}, "") == (
        "GET",
        "https://x.com/home",
    )
    assert social.direct_target("x", "asset", {"asset": "main.js"}, "") == (
        "GET",
        "https://abs.twimg.com/responsive-web/client-web/main.js",
    )
    assert social.direct_target(
        "reddit",
        "listing",
        {"account": "alice", "collection": "saved", "after": None},
        "",
    ) == (
        "GET",
        "https://old.reddit.com/user/alice/saved.json?limit=100&raw_json=1",
    )
    assert social.direct_target(
        "reddit",
        "comments",
        {"permalink": "https://old.reddit.com/r/test/comments/abc123/title"},
        "",
    ) == (
        "GET",
        "https://old.reddit.com/r/test/comments/abc123/title/?context=0",
    )
    assert social.direct_target("youtube", "history", {}, "") == (
        "GET",
        "https://www.youtube.com/feed/history?hl=en",
    )


def test_direct_relay_returns_upstream_result():
    class Raw:
        def read(self, amount, decode_content=True):
            return b'{"ok": true}'

    class Response:
        status_code = 200
        headers = {"Content-Type": "application/json"}
        raw = Raw()

        def close(self):
            pass

    class Session:
        prepared = None
        sent = None

        def prepare_request(self, request):
            self.prepared = request
            return request

        def send(self, prepared, **kwargs):
            self.sent = kwargs
            return Response()

    session = Session()
    result = social.DirectRelay(SimpleNamespace(session=session)).relay(
        "x",
        "home",
        {},
        headers={"Cookie": "auth_token=synthetic"},
    )

    assert session.prepared.url == "https://x.com/home"
    assert session.sent["allow_redirects"] is True
    assert result["ok"] is True
    assert result["transport"] == "direct"
    assert result["bridge_ok"] is False
    assert result["json"] == {"ok": True}
