import hashlib
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from mcp_workers_bridge import youtube_history as history
from mcp_workers_bridge.config import WorkersConfig
from mcp_workers_bridge.errors import ConfigurationError
from mcp_workers_bridge.youtube_cache import CacheManager, atomic_json


VIDEO = "abcDEF123_-"
OTHER = "zyxWVU987_-"
ZONE = "Europe/London"
CONFIG = WorkersConfig(
    base_url="https://bridge.example.org",
    client_id="synthetic-id",
    client_secret="synthetic-secret",
    timeout=30,
)


def bootstrap():
    config = {
        "LOGGED_IN": True,
        "INNERTUBE_CONTEXT": {
            "client": {
                "clientName": "WEB",
                "clientVersion": "synthetic",
                "visitorData": "synthetic-visitor",
            }
        },
        "SESSION_INDEX": 2,
        "DELEGATED_SESSION_ID": "synthetic-page",
        "INNERTUBE_API_KEY": "synthetic_key",
    }
    return "ytcfg.set(" + json.dumps(config) + ");"


def continuation(token="synthetic-next", path="/youtubei/v1/browse"):
    return {
        "continuationItemRenderer": {
            "continuationEndpoint": {
                "commandMetadata": {
                    "webCommandMetadata": {"apiUrl": path}
                },
                "continuationCommand": {"token": token},
            }
        }
    }


def section(label, identifier=VIDEO, title="Synthetic title"):
    return {
        "itemSectionRenderer": {
            "header": {
                "itemSectionHeaderRenderer": {
                    "title": {"simpleText": label}
                }
            },
            "contents": [
                {
                    "videoRenderer": {
                        "videoId": identifier,
                        "title": {"simpleText": title},
                        "ownerText": {"simpleText": "Synthetic channel"},
                    }
                }
            ],
        }
    }


def page(items, initial=False, logged_out=False):
    payload = {
        "responseContext": {
            "mainAppWebResponseContext": {"loggedOut": logged_out}
        }
    }
    if initial:
        payload["contents"] = {
            "twoColumnBrowseResultsRenderer": {
                "tabs": [
                    {
                        "tabRenderer": {
                            "selected": True,
                            "content": {
                                "sectionListRenderer": {"contents": items}
                            },
                        }
                    }
                ]
            }
        }
    else:
        payload["onResponseReceivedActions"] = [
            {
                "appendContinuationItemsAction": {
                    "continuationItems": items
                }
            }
        ]
    return payload


def result(body, status=200):
    return {
        "ok": 200 <= status < 300,
        "bridge_ok": True,
        "service": "youtube",
        "operation": "synthetic",
        "upstream_http": status,
        "content_type": "application/json",
        "body": body if isinstance(body, str) else json.dumps(body),
        "body_truncated": False,
        "_set_cookies": [],
    }


class FakeWorkersClient:
    pages = []
    calls = []

    def __init__(self, config):
        assert config == CONFIG

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def relay(self, service, operation, parameters, **kwargs):
        self.calls.append((service, operation, parameters, kwargs))
        if operation == "history":
            return result(bootstrap())
        return self.pages.pop(0)


@pytest.fixture(autouse=True)
def synthetic_session(monkeypatch):
    monkeypatch.setenv(
        "YOUTUBE_STORAGE_STATE_JSON",
        json.dumps(
            {
                "cookies": [
                    {
                        "name": "SAPISID",
                        "value": "synthetic-sid",
                        "domain": ".youtube.com",
                        "path": "/",
                        "secure": True,
                        "expires": -1,
                    },
                    {
                        "name": "private-google",
                        "value": "must-not-forward",
                        "domain": ".google.com",
                        "path": "/",
                        "secure": True,
                        "expires": -1,
                    },
                ]
            }
        ),
    )
    monkeypatch.setattr(history, "WorkersClient", FakeWorkersClient)
    FakeWorkersClient.pages = []
    FakeWorkersClient.calls = []
    monkeypatch.setattr(history.time, "time", lambda: 2_000_000_000)


def today():
    return datetime.now(ZoneInfo(ZONE)).date()


def test_retrieves_continuation_and_normalizes_video_days(monkeypatch):
    monkeypatch.setattr(history.time, "sleep", lambda _: None)
    current = today()
    FakeWorkersClient.pages = [
        result(page([section("Today"), continuation()], initial=True)),
        result(page([section("Yesterday", OTHER, "Older title")])),
    ]

    output = history.youtube_history(
        CONFIG,
        since=str(current - timedelta(days=1)),
        max_pages=5,
        timezone_name=ZONE,
        profile="Default",
    )

    assert output["ok"] is True
    assert output["partial"] is False
    assert output["video_count"] == 2
    assert output["coverage"]["pages_fetched"] == 2
    assert output["coverage"]["termination_reason"] == "feed_exhausted"
    assert output["coverage"]["range_complete"] is True
    assert {video["video_id"] for video in output["videos"]} == {VIDEO, OTHER}

    operations = [call[1] for call in FakeWorkersClient.calls]
    assert operations == ["history", "browse", "browse"]
    first_browse = FakeWorkersClient.calls[1][3]
    second_browse = FakeWorkersClient.calls[2][3]
    assert json.loads(first_browse["body"])["browseId"] == "FEhistory"
    assert json.loads(second_browse["body"])["continuation"] == "synthetic-next"
    assert first_browse["query"] == "prettyPrint=false&key=synthetic_key"


def test_uses_vault_authentication_context_and_cookie_scope():
    current = today()
    FakeWorkersClient.pages = [
        result(page([section("Today")], initial=True))
    ]

    history.youtube_history(
        CONFIG,
        since=str(current),
        max_pages=1,
        timezone_name="Asia/Tokyo",
        profile="Default",
    )

    bootstrap_call = FakeWorkersClient.calls[0][3]
    browse_call = FakeWorkersClient.calls[1][3]
    assert "SAPISID=synthetic-sid" in bootstrap_call["headers"]["Cookie"]
    assert "must-not-forward" not in bootstrap_call["headers"]["Cookie"]
    digest = hashlib.sha1(
        b"2000000000 synthetic-sid https://www.youtube.com"
    ).hexdigest()
    assert browse_call["headers"]["Authorization"] == (
        "SAPISIDHASH 2000000000_" + digest
    )
    assert browse_call["headers"]["X-Goog-Authuser"] == "2"
    assert browse_call["headers"]["X-Goog-PageId"] == "synthetic-page"
    body = json.loads(browse_call["body"])
    assert body["context"]["client"]["timeZone"] == "Asia/Tokyo"
    assert body["context"]["client"]["utcOffsetMinutes"] == 540


def test_page_cap_returns_visible_partial_coverage():
    current = today()
    FakeWorkersClient.pages = [
        result(page([section("Today"), continuation()], initial=True))
    ]

    output = history.youtube_history(
        CONFIG,
        since=str(current),
        max_pages=1,
        timezone_name=ZONE,
        profile="Default",
    )

    assert output["ok"] is False
    assert output["partial"] is True
    assert output["error"] == "history_incomplete"
    assert output["video_count"] == 1
    assert output["coverage"]["termination_reason"] == "page_cap"
    assert output["coverage"]["range_complete"] is False


def test_refresh_without_worker_uses_direct_transport(monkeypatch):
    class Direct(FakeWorkersClient):
        def __init__(self, state):
            pass

    monkeypatch.setattr(history, "DirectRelay", Direct)
    current = today()
    FakeWorkersClient.pages = [result(page([section("Today")], initial=True))]

    output = history.youtube_history(
        None,
        since=str(current),
        max_pages=1,
        timezone_name=ZONE,
        profile="Default",
    )

    assert output["ok"] is True
    assert [call[1] for call in FakeWorkersClient.calls] == ["history", "browse"]


def test_state_data_uses_chrome_cookies_when_session_unset(monkeypatch):
    monkeypatch.delenv("YOUTUBE_STORAGE_STATE_JSON", raising=False)
    monkeypatch.delenv("LOCAL_CACHE_ROOT", raising=False)
    monkeypatch.setattr(
        history,
        "automatic_storage_state",
        lambda service, profile: {
            "cookies": [
                {
                    "name": "SAPISID",
                    "value": "synthetic-sid",
                    "domain": ".youtube.com",
                    "path": "/",
                    "expires": -1,
                }
            ]
        },
    )
    data = history._state_data("Default")
    assert data["cookies"][0]["name"] == "SAPISID"


def test_state_data_explicit_profile_overrides_environment(monkeypatch):
    monkeypatch.setenv("YOUTUBE_STORAGE_STATE_JSON", '{"cookies":[]}')
    calls = []
    monkeypatch.setattr(
        history,
        "automatic_storage_state",
        lambda service, profile: calls.append((service, profile))
        or {"cookies": [{"name": "SAPISID", "value": "synthetic"}]},
    )
    data = history._state_data("Profile 2")
    assert calls == [("youtube", "Profile 2")]
    assert data["cookies"][0]["value"] == "synthetic"


def test_state_data_rejects_blank_profile():
    with pytest.raises(ConfigurationError, match="profile is required"):
        history._state_data("")


def test_refresh_checkpoints_vault_compatible_local_history(monkeypatch, tmp_path):
    monkeypatch.setattr(history.time, "sleep", lambda _: None)
    current = today()
    FakeWorkersClient.pages = [
        result(page([section("Today")], initial=True))
    ]
    manager = CacheManager(root=tmp_path)
    output = history.youtube_history(
        CONFIG,
        since=str(current),
        max_pages=1,
        timezone_name=ZONE,
        cache_manager=manager,
        profile="Default",
    )
    cached = json.loads((tmp_path / "history.json").read_text())
    assert output["cache"]["local_checkpoint_written"] is True
    assert cached["videos"][0]["video_id"] == VIDEO
    assert cached["videos"][0]["transcript"]["status"] == "unchecked"
    assert cached["metadata"]["cloud_history"]["range_complete"] is True


def test_cache_only_read_does_not_require_worker_credentials(tmp_path):
    current = today()
    observations = {
        (VIDEO, str(current)): {
            "video_id": VIDEO,
            "title": "Cached title",
            "channel": "Synthetic channel",
            "watched_on": str(current),
        }
    }
    index = history.merge_observations(
        {"metadata": {}, "videos": []}, observations
    )
    coverage = history.coverage_for(
        cutoff=current,
        ended=current,
        timezone_name=ZONE,
        complete=True,
        oldest=current,
        unknown=set(),
        failures=[],
        pages=1,
        reason="feed_exhausted",
    )
    index["metadata"]["cloud_history"] = coverage
    manager = CacheManager(root=tmp_path)
    atomic_json(tmp_path / "history.json", index)
    output = history.youtube_history(
        None,
        since=str(current),
        timezone_name=ZONE,
        refresh=False,
        cache_manager=manager,
        profile="Default",
    )
    assert output["ok"] is True
    assert output["cache_only"] is True
    assert output["videos"][0]["title"] == "Cached title"


def test_failed_continuation_preserves_first_page(monkeypatch):
    monkeypatch.setattr(history.time, "sleep", lambda _: None)
    current = today()
    FakeWorkersClient.pages = [
        result(page([section("Today"), continuation()], initial=True)),
        result("", status=503),
        result("", status=503),
        result("", status=503),
    ]

    output = history.youtube_history(
        CONFIG,
        since=str(current),
        max_pages=5,
        timezone_name=ZONE,
        profile="Default",
    )

    assert output["ok"] is False
    assert output["video_count"] == 1
    assert output["coverage"]["pages_fetched"] == 1
    assert output["coverage"]["termination_reason"] == "request_failed"
    assert output["coverage"]["range_complete"] is False
    assert output["coverage"]["errors"] == [
        "YouTube returned HTTP 503 at browse."
    ]


def test_signed_out_history_is_not_returned_as_empty():
    FakeWorkersClient.pages = [
        result(page([], initial=True, logged_out=True))
    ]

    with pytest.raises(history.HistoryError, match="authentication"):
        history.youtube_history(
            CONFIG,
            since=str(today()),
            max_pages=1,
            timezone_name=ZONE,
            profile="Default",
        )


@pytest.mark.parametrize(
    ("since", "max_pages", "timezone"),
    [
        ("not-a-date", 1, ZONE),
        ("2099-01-01", 1, ZONE),
        (None, 0, ZONE),
        (None, 501, ZONE),
        (None, 1, "Invalid/Timezone"),
    ],
)
def test_rejects_invalid_arguments(since, max_pages, timezone):
    with pytest.raises(history.HistoryError):
        history.youtube_history(
            CONFIG,
            since=since,
            max_pages=max_pages,
            timezone_name=timezone,
            profile="Default",
        )


def test_rejects_expired_required_session_cookie(monkeypatch):
    monkeypatch.setenv(
        "YOUTUBE_STORAGE_STATE_JSON",
        json.dumps(
            {
                "cookies": [
                    {
                        "name": "SAPISID",
                        "value": "expired-secret",
                        "domain": ".youtube.com",
                        "path": "/",
                        "expires": 1,
                    }
                ]
            }
        ),
    )
    with pytest.raises(ConfigurationError) as error:
        history.YouTubeCookieState("Default")
    assert "expired-secret" not in str(error.value)


def test_loads_standard_vault_session_with_secure_papisid(monkeypatch, tmp_path):
    monkeypatch.delenv("YOUTUBE_STORAGE_STATE_JSON", raising=False)
    monkeypatch.setenv("LOCAL_CACHE_ROOT", str(tmp_path))
    path = tmp_path / ".codex/tmp/youtube/auth/storage-state.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "cookies": [
                    {
                        "name": "__Secure-3PAPISID",
                        "value": "synthetic-papisid",
                        "domain": ".youtube.com",
                        "path": "/",
                        "secure": True,
                        "expires": -1,
                    }
                ]
            }
        )
    )
    state = history.YouTubeCookieState("Default")
    try:
        assert "__Secure-3PAPISID=synthetic-papisid" in state.cookie_header(
            "POST", history.BROWSE_URL
        )
    finally:
        state.close()


def test_malformed_continuation_and_bootstrap_are_rejected():
    with pytest.raises(history.HistoryError):
        history.continuation_token(
            [continuation(path="https://example.org/collect")]
        )
    with pytest.raises(history.HistoryError):
        history.bootstrap_config(
            'ytcfg.set({"LOGGED_IN":false,"INNERTUBE_CONTEXT":{}});'
        )
