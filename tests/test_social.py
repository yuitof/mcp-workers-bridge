import json

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
    result = social.x_asset(CONFIG, "ondemand.synthetic.js")
    assert "auth_token=must-not-escape" not in str(result)
