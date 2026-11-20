"""High-level authenticated X Likes and Bookmarks collection through Workers."""

from __future__ import annotations

from datetime import datetime, timezone
import html
import json
import re
from typing import Any
from urllib.parse import urlencode

from bs4 import BeautifulSoup
from x_client_transaction import ClientTransaction

from .config import WorkersConfig
from .errors import BridgeError, UpstreamError
from .social import CookieState, X_USER_AGENT
from .workers import WorkersClient


POST_ID = re.compile(r"[1-9][0-9]{0,24}\Z")
QUERY_ID = re.compile(r"[-_A-Za-z0-9]{1,64}\Z")
CURSOR = re.compile(r"[-_A-Za-z0-9]{1,8192}\Z")
READ_OPERATIONS = {"Viewer", "Likes", "Bookmarks"}
COLLECTION_OPERATIONS = {"likes": "Likes", "bookmarks": "Bookmarks"}
ALLOWED_HEADERS = {
    "accept",
    "accept-language",
    "authorization",
    "cookie",
    "referer",
    "user-agent",
    "x-client-transaction-id",
    "x-csrf-token",
    "x-twitter-active-user",
    "x-twitter-auth-type",
    "x-twitter-client-language",
}


class XCollectionError(BridgeError):
    def __init__(self, code: str, message: str):
        super().__init__(code, message)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def walk(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def operations(source: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    pattern = (
        r'queryId:"([-_A-Za-z0-9]+)",operationName:"([A-Za-z0-9_]+)",'
        r'operationType:"query",metadata:\{featureSwitches:(\[[^\]]*\]),'
        r"fieldToggles:(\[[^\]]*\])"
    )
    for identifier, name, switches, toggles in re.findall(pattern, source):
        if name not in READ_OPERATIONS:
            continue
        try:
            feature_switches = json.loads(switches)
            field_toggles = json.loads(toggles)
        except ValueError:
            continue
        if (
            QUERY_ID.fullmatch(identifier)
            and isinstance(feature_switches, list)
            and all(isinstance(item, str) for item in feature_switches)
            and isinstance(field_toggles, list)
            and all(isinstance(item, str) for item in field_toggles)
        ):
            result[name] = {
                "query_id": identifier,
                "switches": feature_switches,
                "toggles": field_toggles,
            }
    return result


def chunk_assets(source: str) -> list[str]:
    """Read Webpack asset tables as data without evaluating page JavaScript."""
    pairs = re.findall(r'(\d+):"([^"\n]+)"', source)
    names = {
        key: value
        for key, value in pairs
        if value.startswith(("bundle.", "shared~", "ondemand."))
    }
    hashes = {
        key: value
        for key, value in pairs
        if re.fullmatch(r"[a-f0-9]{8,32}", value)
    }
    assets = []
    for key, name in names.items():
        if key not in hashes or ("Bookmark" not in name and name != "ondemand.s"):
            continue
        asset = f"{name}.{hashes[key]}a.js"
        if re.fullmatch(
            r"(?:main|bundle|shared~|ondemand)[A-Za-z0-9._~-]{1,160}\.js",
            asset,
        ):
            assets.append(asset)
    return list(dict.fromkeys(assets))


def initial_state(source: str) -> dict[str, Any]:
    match = re.search(r"window\.__INITIAL_STATE__\s*=\s*", source)
    if not match:
        raise XCollectionError(
            "x_bootstrap_failed",
            "X did not serve the supported authenticated web build.",
        )
    try:
        value, _ = json.JSONDecoder().raw_decode(source[match.end() :])
    except (ValueError, TypeError):
        raise XCollectionError(
            "x_bootstrap_failed", "X initial state could not be parsed."
        ) from None
    if not isinstance(value, dict):
        raise XCollectionError("x_bootstrap_failed", "X initial state is invalid.")
    return value


def unwrap_tweet(result: Any) -> dict[str, Any]:
    while (
        isinstance(result, dict)
        and result.get("__typename") == "TweetWithVisibilityResults"
    ):
        result = result.get("tweet", {})
    return result if isinstance(result, dict) else {}


def normalize_post(result: Any, retrieved_at: str) -> dict[str, Any] | None:
    """Return content fields only: never request headers or the full timeline."""
    result = unwrap_tweet(result)
    identifier = result.get("rest_id", "")
    legacy = result.get("legacy", {})
    if (
        not POST_ID.fullmatch(str(identifier))
        or not isinstance(legacy.get("full_text"), str)
    ):
        return None
    author = result.get("core", {}).get("user_results", {}).get("result", {})
    if not isinstance(author, dict):
        author = {}
    core = author.get("core", {}) or author.get("legacy", {})
    if not isinstance(core, dict):
        core = {}
    handle = core.get("screen_name", "")
    note = (
        result.get("note_tweet", {})
        .get("note_tweet_results", {})
        .get("result", {})
    )
    if not isinstance(note, dict):
        note = {}
    long_text = note.get("text")
    post = {
        "schema_version": 1,
        "id": str(identifier),
        "source_url": f"https://x.com/{handle or 'i/web'}/status/{identifier}",
        "retrieved_at": retrieved_at,
        "created_at": legacy.get("created_at"),
        "author": {
            "id": author.get("rest_id"),
            "screen_name": handle,
            "name": core.get("name", ""),
        },
        "text": html.unescape(
            long_text if isinstance(long_text, str) else legacy["full_text"]
        ),
        "text_source": (
            "note_tweet" if isinstance(long_text, str) else "legacy.full_text"
        ),
        "language": legacy.get("lang"),
        "entities": note.get("entity_set") or legacy.get("entities", {}),
        "media": legacy.get("extended_entities", {}).get("media", []),
        "reply_to_post_id": legacy.get("in_reply_to_status_id_str"),
        "quoted_post_id": legacy.get("quoted_status_id_str"),
        "reposted_post_id": None,
    }
    repost = unwrap_tweet(
        legacy.get("retweeted_status_result", {}).get("result", {})
    )
    if repost.get("rest_id"):
        post["reposted_post_id"] = str(repost["rest_id"])
    article = result.get("article", {}).get("article_results", {}).get("result")
    if isinstance(article, dict):
        post["article"] = {
            key: article[key]
            for key in (
                "rest_id",
                "title",
                "preview_text",
                "plain_text",
                "content_state",
                "cover_media",
            )
            if key in article
        }
    if isinstance(note.get("richtext"), dict):
        post["richtext"] = note["richtext"]
    card = result.get("card", {}).get("legacy")
    if isinstance(card, dict):
        post["card"] = {
            key: card[key]
            for key in ("name", "url", "binding_values")
            if key in card
        }
    return post


def embedded_posts(result: Any, retrieved_at: str) -> dict[str, dict[str, Any]]:
    posts = {}
    for item in walk(result):
        if item.get("__typename") == "Tweet":
            post = normalize_post(item, retrieved_at)
            if post:
                posts[post["id"]] = post
    return posts


def parse_timeline(
    data: dict[str, Any], retrieved_at: str
) -> dict[str, Any]:
    instructions = [
        item["instructions"]
        for item in walk(data)
        if isinstance(item.get("instructions"), list)
    ]
    if not instructions:
        raise XCollectionError(
            "x_schema_error",
            "X timeline schema is missing instructions; no empty result was inferred.",
        )
    entries: list[dict[str, Any]] = []
    terminated = False
    recognized = False
    for group in instructions:
        for instruction in group:
            if not isinstance(instruction, dict):
                continue
            kind = instruction.get("type")
            if kind in (
                "TimelineTerminateTimeline",
                "TimelineAddEntries",
                "TimelineReplaceEntry",
                "TimelineAddToModule",
            ):
                recognized = True
            if (
                kind == "TimelineTerminateTimeline"
                and instruction.get("direction") == "Bottom"
            ):
                terminated = True
            if kind == "TimelineAddEntries":
                values = instruction.get("entries", [])
                entries.extend(item for item in values if isinstance(item, dict))
            elif kind == "TimelineReplaceEntry":
                value = instruction.get("entry", {})
                if isinstance(value, dict):
                    entries.append(value)
            elif kind == "TimelineAddToModule":
                values = instruction.get("moduleItems", [])
                entries.extend(item for item in values if isinstance(item, dict))
    if not recognized:
        raise XCollectionError(
            "x_schema_error",
            "X timeline has no recognized update instruction.",
        )
    cursor_only = bool(entries) and all(
        isinstance(entry.get("content"), dict)
        and entry["content"].get("cursorType") in ("Top", "Bottom")
        for entry in entries
    )
    members: list[str] = []
    posts: dict[str, dict[str, Any]] = {}
    unavailable: list[str] = []
    cursor = None
    for entry in entries:
        content = entry.get("content", entry)
        if any(
            "promotedMetadata" in item or "promoted_metadata" in item
            for item in walk(content)
        ):
            continue
        for item in walk(content):
            if item.get("cursorType") == "Bottom" and isinstance(
                item.get("value"), str
            ):
                cursor = item["value"]
            result = item.get("tweet_results", {}).get("result")
            if result is None or not (
                item.get("itemType") == "TimelineTweet"
                or item.get("__typename") == "TimelineTweet"
            ):
                continue
            post = normalize_post(result, retrieved_at)
            posts.update(embedded_posts(result, retrieved_at))
            if post:
                posts[post["id"]] = post
                if post["id"] not in members:
                    members.append(post["id"])
            else:
                identifier = entry.get("entryId", "").removeprefix("tweet-")
                if POST_ID.fullmatch(identifier):
                    unavailable.append(identifier)
    return {
        "members": members,
        "posts": posts,
        "cursor": None if terminated else cursor,
        "unavailable": unavailable,
        "cursor_only": cursor_only,
    }


class XCollectionClient:
    def __init__(self, config: WorkersConfig):
        self.config = config
        self.state = CookieState("x")
        self.client = WorkersClient(config)
        self.features: dict[str, bool] = {}
        self.operations: dict[str, dict[str, Any]] = {}
        self.account_id: str | None = None
        self.headers: dict[str, str] = {}
        self.transaction: ClientTransaction | None = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.client.close()
        self.state.close()

    def _request_headers(self) -> dict[str, str]:
        return {
            "User-Agent": X_USER_AGENT,
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
        }

    def _home(self) -> str:
        result = self.client.relay(
            "x",
            "home",
            {},
            headers=self.state.headers(
                "https://x.com/home", self._request_headers()
            ),
            max_bytes=4_000_000,
        )
        self.state.consume(result, "https://x.com/home")
        self._require_success(result, "X home")
        if result.get("body_truncated"):
            raise XCollectionError(
                "x_response_too_large", "X home response exceeded the supported size."
            )
        return result.get("body", "")

    def _asset(self, asset: str) -> str:
        result = self.client.relay(
            "x",
            "asset",
            {"asset": asset},
            headers=self._request_headers(),
            max_bytes=12_000_000,
        )
        result.pop("_set_cookies", None)
        self._require_success(result, "X asset")
        if result.get("body_truncated"):
            raise XCollectionError(
                "x_response_too_large",
                f"X asset {asset} exceeded the supported size.",
            )
        return result.get("body", "")

    def _require_success(self, result: dict[str, Any], stage: str):
        if result.get("ok") is True:
            return
        status = result.get("upstream_http")
        if isinstance(status, int):
            if status == 429:
                raise XCollectionError(
                    "x_rate_limited",
                    "X rate limit was reached; resume later with the returned cursor.",
                )
            if status in (401, 403):
                raise XCollectionError(
                    "x_session_expired",
                    "X login expired, access was denied, or a challenge was returned.",
                )
            raise UpstreamError(stage, status)
        raise XCollectionError("x_request_failed", f"{stage} request failed.")

    def bootstrap(self):
        if self.transaction is not None:
            return
        home = self._home()
        state = initial_state(home)
        self.account_id = str(state.get("session", {}).get("user_id", ""))
        if not POST_ID.fullmatch(self.account_id):
            raise XCollectionError(
                "x_authentication_error",
                "X home did not contain an authenticated account identity.",
            )
        feature_switch = state.get("featureSwitch", {})
        for values in (
            feature_switch.get("defaultConfig", {}),
            feature_switch.get("user", {}).get("config", {}),
        ):
            if not isinstance(values, dict):
                continue
            for name, value in values.items():
                value = value.get("value") if isinstance(value, dict) else value
                if isinstance(value, bool):
                    self.features[name] = value
        main_assets = re.findall(
            r"https://abs\.twimg\.com/responsive-web/client-web/"
            r"(main\.[a-zA-Z0-9]+\.js)",
            home,
        )
        if not main_assets:
            raise XCollectionError(
                "x_bootstrap_failed",
                "Authenticated X web bundle was not advertised.",
            )
        main = self._asset(main_assets[0])
        self.operations.update(operations(main))
        ondemand = None
        for asset in chunk_assets(home)[:16]:
            source = self._asset(asset)
            self.operations.update(operations(source))
            if asset.startswith("ondemand.s."):
                ondemand = source
        bearer = re.search(r"AAAA[A-Za-z0-9%_-]{50,}", main)
        if not bearer or not ondemand:
            raise XCollectionError(
                "x_bootstrap_failed",
                "X bearer or transaction assets changed.",
            )
        try:
            self.transaction = ClientTransaction(
                BeautifulSoup(home, "html.parser"), ondemand
            )
        except Exception:
            raise XCollectionError(
                "x_bootstrap_failed",
                "X transaction-header algorithm changed; update the adapter.",
            ) from None
        cookie_header = self.state.headers(
            "https://x.com/home", self._request_headers()
        ).get("Cookie", "")
        cookie_values = dict(
            re.findall(r"(?:^|;\s*)([^=;]+)=([^;]*)", cookie_header)
        )
        csrf = cookie_values.get("ct0")
        if not csrf:
            raise XCollectionError(
                "x_authentication_error", "X CSRF cookie is missing."
            )
        self.headers = {
            "Authorization": "Bearer " + bearer.group(),
            "x-csrf-token": csrf,
            "x-twitter-auth-type": "OAuth2Session",
            "x-twitter-active-user": "yes",
            "x-twitter-client-language": "en",
            "Referer": "https://x.com/",
        }

    def query(self, operation: str, variables: dict[str, Any]) -> dict[str, Any]:
        if operation not in READ_OPERATIONS:
            raise XCollectionError(
                "invalid_argument", "Unsupported high-level X operation."
            )
        self.bootstrap()
        spec = self.operations.get(operation)
        if not spec:
            raise XCollectionError(
                "x_operation_missing",
                f"{operation} was not discovered in the current X web build.",
            )
        path = f"/i/api/graphql/{spec['query_id']}/{operation}"
        headers = dict(self.headers)
        try:
            headers["x-client-transaction-id"] = (
                self.transaction.generate_transaction_id(method="GET", path=path)
            )
        except Exception:
            raise XCollectionError(
                "x_transaction_error",
                "Could not generate the X transaction header.",
            ) from None
        toggles = {name: False for name in spec["toggles"]}
        for name in ("withArticleRichContentState", "withArticlePlainText"):
            if name in toggles:
                toggles[name] = True
        query = urlencode(
            {
                "variables": json.dumps(
                    variables, separators=(",", ":")
                ),
                "features": json.dumps(
                    {
                        name: self.features.get(name, False)
                        for name in spec["switches"]
                    },
                    separators=(",", ":"),
                ),
                "fieldToggles": json.dumps(toggles, separators=(",", ":")),
            }
        )
        url = "https://x.com" + path
        result = self.client.relay(
            "x",
            "graphql",
            {
                "queryId": spec["query_id"],
                "operationName": operation,
            },
            query=query,
            headers=self.state.headers(url, headers),
            max_bytes=12_000_000,
        )
        self.state.consume(result, url)
        self._require_success(result, f"X {operation}")
        if result.get("body_truncated"):
            raise XCollectionError(
                "x_response_too_large",
                f"X {operation} response exceeded the supported size.",
            )
        try:
            payload = result.get("json") or json.loads(result.get("body", ""))
        except ValueError:
            raise XCollectionError(
                "x_schema_error", "X returned invalid GraphQL JSON."
            ) from None
        if not isinstance(payload, dict):
            raise XCollectionError(
                "x_schema_error", "X returned an unexpected GraphQL response."
            )
        errors = payload.get("errors", [])
        if errors:
            codes = [
                item.get("code")
                for item in errors
                if isinstance(item, dict) and isinstance(item.get("code"), int)
            ]
            raise XCollectionError(
                "x_graphql_error",
                f"X {operation} GraphQL error codes {codes}; partial data was rejected.",
            )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise XCollectionError(
                "x_schema_error", "X GraphQL response has no data object."
            )
        return data

    def verify_viewer(self):
        data = self.query("Viewer", {"withCommunitiesMemberships": False})
        user = (
            data.get("viewer", {})
            .get("user_results", {})
            .get("result", {})
        )
        identifier = str(user.get("rest_id", "")) if isinstance(user, dict) else ""
        if identifier != self.account_id:
            raise XCollectionError(
                "x_identity_mismatch",
                "Viewer identity differs from the authenticated home page.",
            )

    def collection(
        self,
        name: str,
        *,
        max_pages: int,
        cursor: str | None,
        count: int,
    ) -> dict[str, Any]:
        if name not in COLLECTION_OPERATIONS:
            raise XCollectionError(
                "invalid_argument", "collection must be likes or bookmarks."
            )
        if not 1 <= max_pages <= 200:
            raise XCollectionError(
                "invalid_argument", "max_pages must be between 1 and 200."
            )
        if not 1 <= count <= 100:
            raise XCollectionError(
                "invalid_argument", "count must be between 1 and 100."
            )
        if cursor is not None and not CURSOR.fullmatch(cursor):
            raise XCollectionError(
                "invalid_argument", "cursor is not a valid X continuation token."
            )
        self.bootstrap()
        self.verify_viewer()
        operation = COLLECTION_OPERATIONS[name]
        current = cursor
        seen = {current} if current else set()
        posts: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        unavailable: set[str] = set()
        pages = 0
        complete = False
        reason = "page_limit"
        while pages < max_pages:
            variables: dict[str, Any] = {
                "count": count,
                "includePromotedContent": False,
            }
            if current:
                variables["cursor"] = current
            if operation == "Likes":
                variables.update(
                    userId=self.account_id,
                    withQuickPromoteEligibilityTweetFields=True,
                    withVoice=True,
                    withV2Timeline=True,
                )
            data = self.query(operation, variables)
            page = parse_timeline(data, now())
            pages += 1
            unavailable.update(page["unavailable"])
            for identifier in page["members"]:
                if identifier not in posts and identifier in page["posts"]:
                    order.append(identifier)
            posts.update(page["posts"])
            next_cursor = page["cursor"]
            if page["cursor_only"] or not next_cursor:
                complete = True
                reason = (
                    "cursor_only_terminal_page"
                    if page["cursor_only"]
                    else "no_bottom_continuation"
                )
                current = None
                break
            if next_cursor == current or next_cursor in seen:
                reason = "repeated_cursor"
                break
            current = next_cursor
            seen.add(current)
        result = {
            "ok": complete,
            "partial": not complete,
            "collection": name,
            "account_verified": True,
            "pages_fetched": pages,
            "post_count": len(order),
            "posts": [posts[identifier] for identifier in order],
            "next_cursor": current,
            "unavailable_post_ids": sorted(unavailable),
            "termination_reason": reason,
            "coverage_note": (
                "Only posts exposed by X's collection API were returned. "
                "A terminal page does not prove lifetime completeness."
            ),
        }
        if not complete:
            result.update(
                error="collection_incomplete",
                message=(
                    "X collection pagination is incomplete; resume with "
                    "next_cursor."
                ),
            )
        return result


def collect_x_collection(
    config: WorkersConfig,
    collection: str,
    *,
    max_pages: int = 20,
    cursor: str | None = None,
    count: int = 100,
) -> dict[str, Any]:
    with XCollectionClient(config) as client:
        return client.collection(
            collection,
            max_pages=max_pages,
            cursor=cursor,
            count=count,
        )
