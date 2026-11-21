import json

import pytest

from mcp_workers_bridge import x_collections
from mcp_workers_bridge.config import WorkersConfig
from mcp_workers_bridge.x_collections import (
    XCollectionClient,
    chunk_assets,
    collect_x_collection,
    normalize_post,
    operations,
    parse_timeline,
)


CONFIG = WorkersConfig(
    base_url="https://bridge.example.org",
    client_id="synthetic-id",
    client_secret="synthetic-secret",
    timeout=30,
)
ACCOUNT = "42"
POST = "1234567890123456789"
OTHER = "2234567890123456789"


def tweet(identifier=POST, text="Synthetic sentence.", promoted=False):
    return {
        "entryId": f"tweet-{identifier}",
        "content": {
            "itemContent": {
                "itemType": "TimelineTweet",
                "tweet_results": {
                    "result": {
                        "__typename": "Tweet",
                        "rest_id": identifier,
                        "core": {
                            "user_results": {
                                "result": {
                                    "rest_id": ACCOUNT,
                                    "core": {
                                        "screen_name": "fixture",
                                        "name": "Fixture",
                                    },
                                }
                            }
                        },
                        "legacy": {
                            "full_text": text,
                            "lang": "en",
                            "created_at": "2026-11-20T00:00:00Z",
                            "entities": {},
                            "extended_entities": {"media": []},
                        },
                        **({"promotedMetadata": {}} if promoted else {}),
                    }
                },
            }
        },
    }


def cursor(value="next-token"):
    return {
        "entryId": "cursor-bottom",
        "content": {"cursorType": "Bottom", "value": value},
    }


def page(entries, terminate=False):
    instructions = [{"type": "TimelineAddEntries", "entries": entries}]
    if terminate:
        instructions.append(
            {"type": "TimelineTerminateTimeline", "direction": "Bottom"}
        )
    return {"timeline": {"instructions": instructions}}


def operation(name, identifier):
    return (
        f'queryId:"{identifier}",operationName:"{name}",operationType:"query",'
        'metadata:{featureSwitches:["feature_a"],fieldToggles:["toggle_a"]}'
    )


def test_operations_are_discovered_without_evaluating_javascript():
    source = (
        operation("Likes", "likes123")
        + operation("Bookmarks", "bookmarks123")
        + operation("CreateTweet", "mutation123")
    )
    discovered = operations(source)
    assert set(discovered) == {"Likes", "Bookmarks"}
    assert discovered["Likes"] == {
        "query_id": "likes123",
        "switches": ["feature_a"],
        "toggles": ["toggle_a"],
    }


def test_chunk_assets_only_selects_bookmarks_and_transaction_bundle():
    source = (
        'x={1:"bundle.Bookmarks",2:"ondemand.s",3:"bundle.Unrelated"};'
        'y={1:"abcdef12",2:"1234abcd",3:"abcd1234"}'
    )
    assert chunk_assets(source) == [
        "bundle.Bookmarks.abcdef12a.js",
        "ondemand.s.1234abcda.js",
    ]


def test_timeline_normalizes_posts_and_filters_promotion():
    result = parse_timeline(
        page([tweet(), tweet(OTHER, promoted=True), cursor()]), "2026-11-20T01:00:00Z"
    )
    assert result["members"] == [POST]
    assert result["posts"][POST]["text"] == "Synthetic sentence."
    assert result["posts"][POST]["author"]["screen_name"] == "fixture"
    assert result["cursor"] == "next-token"
    assert result["cursor_only"] is False


def test_cursor_only_page_is_terminal():
    result = parse_timeline(page([cursor()]), "2026-11-20T01:00:00Z")
    assert result["members"] == []
    assert result["cursor_only"] is True


def test_normalize_rejects_non_tweet():
    assert normalize_post({"legacy": {"full_text": "missing id"}}, "now") is None


def test_collection_paginates_and_adds_likes_variables():
    client = object.__new__(XCollectionClient)
    client.account_id = ACCOUNT
    client.bootstrap = lambda: None
    client.verify_viewer = lambda: None
    calls = []
    responses = [
        page([tweet(), cursor("next-page")]),
        page([tweet(OTHER, "Second page.")], terminate=True),
    ]

    def query(operation_name, variables):
        calls.append((operation_name, variables))
        return responses.pop(0)

    client.query = query
    result = client.collection("likes", max_pages=5, cursor=None, count=100)
    assert result["ok"] is True
    assert result["partial"] is False
    assert result["pages_fetched"] == 2
    assert result["post_count"] == 2
    assert {post["id"] for post in result["posts"]} == {POST, OTHER}
    assert calls[0][0] == "Likes"
    assert calls[0][1]["userId"] == ACCOUNT
    assert calls[1][1]["cursor"] == "next-page"


def test_page_limit_returns_resume_cursor():
    client = object.__new__(XCollectionClient)
    client.account_id = ACCOUNT
    client.bootstrap = lambda: None
    client.verify_viewer = lambda: None
    client.query = lambda operation_name, variables: page(
        [tweet(), cursor("resume-token")]
    )
    result = client.collection(
        "bookmarks", max_pages=1, cursor=None, count=100
    )
    assert result["ok"] is False
    assert result["partial"] is True
    assert result["next_cursor"] == "resume-token"
    assert result["termination_reason"] == "page_limit"


def test_repeated_cursor_is_partial():
    client = object.__new__(XCollectionClient)
    client.account_id = ACCOUNT
    client.bootstrap = lambda: None
    client.verify_viewer = lambda: None
    client.query = lambda operation_name, variables: page(
        [tweet(), cursor("same-token")]
    )
    result = client.collection(
        "bookmarks", max_pages=5, cursor="same-token", count=100
    )
    assert result["partial"] is True
    assert result["termination_reason"] == "repeated_cursor"


def test_end_to_end_discovery_and_collection(monkeypatch):
    monkeypatch.setenv(
        "X_STORAGE_STATE_JSON",
        json.dumps(
            {
                "cookies": [
                    {
                        "name": "auth_token",
                        "value": "synthetic-auth",
                        "domain": ".x.com",
                        "path": "/",
                        "expires": -1,
                    },
                    {
                        "name": "ct0",
                        "value": "synthetic-csrf",
                        "domain": ".x.com",
                        "path": "/",
                        "expires": -1,
                    },
                ]
            }
        ),
    )
    home = (
        'window.__INITIAL_STATE__='
        + json.dumps(
            {
                "session": {"user_id": ACCOUNT},
                "featureSwitch": {
                    "defaultConfig": {
                        "feature_a": {"value": True}
                    }
                },
            }
        )
        + ';https://abs.twimg.com/responsive-web/client-web/main.abc123.js;'
        + 'x={1:"bundle.Bookmarks",2:"ondemand.s"};'
        + 'y={1:"abcdef12",2:"1234abcd"}'
    )
    main = (
        operation("Viewer", "viewer123")
        + operation("Likes", "likes123")
        + "AAAA"
        + "a" * 60
    )
    bookmark_bundle = operation("Bookmarks", "bookmarks123")
    replies = {
        ("home", None): home,
        ("asset", "main.abc123.js"): main,
        ("asset", "bundle.Bookmarks.abcdef12a.js"): bookmark_bundle,
        ("asset", "ondemand.s.1234abcda.js"): "transaction source",
        ("graphql", "Viewer"): {
            "data": {
                "viewer": {
                    "user_results": {
                        "result": {"rest_id": ACCOUNT}
                    }
                }
            }
        },
        ("graphql", "Likes"): {"data": page([tweet()], terminate=True)},
    }
    relay_calls = []

    class FakeWorkersClient:
        def __init__(self, config):
            pass

        def close(self):
            pass

        def relay(self, service, operation_name, parameters, **kwargs):
            relay_calls.append((operation_name, parameters, kwargs))
            if operation_name == "home":
                body = replies[("home", None)]
            elif operation_name == "asset":
                body = replies[("asset", parameters["asset"])]
            else:
                payload = replies[("graphql", parameters["operationName"])]
                body = json.dumps(payload)
            return {
                "ok": True,
                "bridge_ok": True,
                "upstream_http": 200,
                "content_type": "application/json",
                "body": body,
                "body_truncated": False,
                "json": json.loads(body) if body.startswith("{") else None,
                "_set_cookies": [],
            }

    class FakeTransaction:
        def __init__(self, *_args):
            pass

        def generate_transaction_id(self, **_kwargs):
            return "synthetic-transaction"

    monkeypatch.setattr(x_collections, "WorkersClient", FakeWorkersClient)
    monkeypatch.setattr(x_collections, "ClientTransaction", FakeTransaction)
    result = collect_x_collection(
        CONFIG, "likes", max_pages=1, profile="Default"
    )

    assert result["ok"] is True
    assert result["post_count"] == 1
    assert result["posts"][0]["id"] == POST
    assert any(
        kwargs["headers"]["x-client-transaction-id"]
        == "synthetic-transaction"
        for operation_name, _, kwargs in relay_calls
        if operation_name == "graphql"
    )
