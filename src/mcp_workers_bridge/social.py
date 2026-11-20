"""Private Cookie-backed X and Reddit operations over the Worker relay."""

from email.message import Message
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import quote, unquote, urlencode, urlsplit

import requests
from requests.cookies import MockRequest, MockResponse

from .errors import BridgeError, ConfigurationError
from .workers import WorkersClient


X_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
REDDIT_USER_AGENT = (
    "english-vault-personal-archive/1.0 (read-only Old Reddit client)"
)
SESSION_COOKIES = {
    "x": {"auth_token", "ct0"},
    "reddit": {"reddit_session"},
}
DOMAINS = {
    "x": {"x.com", "www.x.com"},
    "reddit": {"reddit.com", "www.reddit.com", "old.reddit.com"},
}
ENV_NAMES = {
    "x": "X_STORAGE_STATE_JSON",
    "reddit": "REDDIT_STORAGE_STATE_JSON",
}


class CookieState:
    def __init__(self, service: str):
        if service not in SESSION_COOKIES:
            raise ValueError("Unknown social service.")
        self.service = service
        self.session = requests.Session()
        self.session.auth = lambda request: request
        self.session.trust_env = False
        raw = os.environ.get(ENV_NAMES[service])
        if raw:
            try:
                data = json.loads(raw)
            except ValueError:
                raise ConfigurationError(f"{ENV_NAMES[service]} is not valid JSON.") from None
        else:
            root_value = os.environ.get("VAULT_ROOT")
            if not root_value:
                raise ConfigurationError(
                    f"Set {ENV_NAMES[service]} or VAULT_ROOT to load the private session."
                )
            path = (
                Path(root_value).expanduser()
                / ".codex/tmp"
                / service
                / "auth/storage-state.json"
            )
            if not path.is_file():
                raise ConfigurationError(
                    f"No private {service} storage-state file was found."
                )
            try:
                data = json.loads(path.read_text())
            except (OSError, ValueError):
                raise ConfigurationError(
                    f"The private {service} storage-state file is invalid."
                ) from None
        now = time.time()
        cookies = [
            cookie
            for cookie in data.get("cookies", [])
            if cookie.get("domain", "").lstrip(".") in DOMAINS[service]
            and cookie.get("name")
            and isinstance(cookie.get("value"), str)
            and (cookie.get("expires", -1) <= 0 or cookie["expires"] > now)
        ]
        if not SESSION_COOKIES[service] <= {cookie["name"] for cookie in cookies}:
            raise ConfigurationError(
                f"The private {service} session is missing required unexpired cookies."
            )
        for cookie in cookies:
            self.session.cookies.set(
                cookie["name"],
                cookie["value"],
                domain=cookie["domain"],
                path=cookie.get("path") or "/",
            )

    def headers(self, url: str, headers: dict[str, str]) -> dict[str, str]:
        prepared = self.session.prepare_request(
            requests.Request("GET", url, headers=headers)
        )
        if prepared.headers.get("Cookie"):
            headers["Cookie"] = prepared.headers["Cookie"]
        return {
            key: value
            for key, value in headers.items()
            if key.lower()
            in {
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
        }

    def consume(self, response: dict, url: str):
        encoded = response.pop("_set_cookies", [])
        if not encoded:
            return
        prepared = self.session.prepare_request(requests.Request("GET", url))
        message = Message()
        for value in encoded:
            message.add_header("Set-Cookie", unquote(value))
        self.session.cookies.extract_cookies(
            MockResponse(message), MockRequest(prepared)
        )

    def close(self):
        self.session.close()


def _x_headers(extra: dict[str, str] | None = None):
    headers = {
        "User-Agent": X_USER_AGENT,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }
    headers.update(extra or {})
    return headers


def _reddit_headers(extra: dict[str, str] | None = None):
    headers = {
        "User-Agent": REDDIT_USER_AGENT,
        "Accept": "application/json",
    }
    headers.update(extra or {})
    return headers


def x_home(config, headers: dict[str, str] | None = None, max_bytes: int = 2_000_000):
    state = CookieState("x")
    try:
        request_headers = state.headers(
            "https://x.com/home", _x_headers(headers)
        )
        with WorkersClient(config) as client:
            result = client.relay(
                "x", "home", {}, headers=request_headers, max_bytes=max_bytes
            )
        state.consume(result, "https://x.com/home")
        return result
    finally:
        state.close()


def x_graphql(
    config,
    query_id: str,
    operation_name: str,
    variables: dict,
    features: dict,
    field_toggles: dict,
    headers: dict[str, str] | None = None,
    max_bytes: int = 4_000_000,
):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", query_id):
        raise BridgeError("invalid_argument", "Invalid X GraphQL query ID.")
    query = urlencode(
        {
            "variables": json.dumps(variables, separators=(",", ":")),
            "features": json.dumps(features, separators=(",", ":")),
            "fieldToggles": json.dumps(field_toggles, separators=(",", ":")),
        }
    )
    url = f"https://x.com/i/api/graphql/{query_id}/{operation_name}"
    state = CookieState("x")
    try:
        request_headers = state.headers(url, _x_headers(headers))
        with WorkersClient(config) as client:
            result = client.relay(
                "x",
                "graphql",
                {"queryId": query_id, "operationName": operation_name},
                query=query,
                headers=request_headers,
                max_bytes=max_bytes,
            )
        state.consume(result, url)
        return result
    finally:
        state.close()


def x_asset(
    config,
    asset: str,
    headers: dict[str, str] | None = None,
    max_bytes: int = 4_000_000,
):
    if not re.fullmatch(r"(?:main|bundle|shared~|ondemand)[A-Za-z0-9._~-]{1,160}\.js", asset):
        raise BridgeError("invalid_argument", "Invalid X client asset name.")
    request_headers = _x_headers(headers)
    request_headers.pop("Cookie", None)
    with WorkersClient(config) as client:
        result = client.relay(
            "x",
            "asset",
            {"asset": asset},
            headers=request_headers,
            max_bytes=max_bytes,
        )
    result.pop("_set_cookies", None)
    return result


def reddit_identity(
    config,
    headers: dict[str, str] | None = None,
    max_bytes: int = 1_000_000,
):
    state = CookieState("reddit")
    try:
        url = "https://old.reddit.com/api/me.json"
        request_headers = state.headers(url, _reddit_headers(headers))
        with WorkersClient(config) as client:
            result = client.relay(
                "reddit", "identity", {}, headers=request_headers, max_bytes=max_bytes
            )
        state.consume(result, url)
        return result
    finally:
        state.close()


def reddit_listing(
    config,
    account: str,
    collection: str,
    after: str | None = None,
    headers: dict[str, str] | None = None,
    max_bytes: int = 8_000_000,
):
    if not re.fullmatch(r"[A-Za-z0-9_-]{3,20}", account):
        raise BridgeError("invalid_argument", "Invalid Reddit account.")
    if collection not in {"upvoted", "saved", "downvoted", "hidden", "submitted", "comments"}:
        raise BridgeError("invalid_argument", "Invalid Reddit collection.")
    if after is not None and not re.fullmatch(r"t[13]_[a-z0-9]{1,16}", after):
        raise BridgeError("invalid_argument", "Invalid Reddit continuation cursor.")
    url = f"https://old.reddit.com/user/{account}/{collection}.json"
    state = CookieState("reddit")
    try:
        request_headers = state.headers(url, _reddit_headers(headers))
        with WorkersClient(config) as client:
            result = client.relay(
                "reddit",
                "listing",
                {"account": account, "collection": collection, "after": after},
                headers=request_headers,
                max_bytes=max_bytes,
            )
        state.consume(result, url)
        return result
    finally:
        state.close()


def reddit_comments(
    config,
    permalink: str,
    headers: dict[str, str] | None = None,
    max_bytes: int = 12_000_000,
):
    parsed = urlsplit(permalink)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in DOMAINS["reddit"]
        or parsed.port not in (None, 443)
        or parsed.username
        or parsed.password
        or not re.fullmatch(
            r"/(?:r/[A-Za-z0-9_]+/)?comments/[a-z0-9]{1,16}"
            r"(?:/[A-Za-z0-9._~%+-]+(?:/[a-z0-9]{1,16})?)?/?",
            parsed.path,
        )
    ):
        raise BridgeError("invalid_argument", "Invalid Reddit comments permalink.")
    state = CookieState("reddit")
    try:
        request_headers = state.headers(
            "https://old.reddit.com" + parsed.path, _reddit_headers(headers)
        )
        request_headers["Accept"] = "text/html"
        with WorkersClient(config) as client:
            result = client.relay(
                "reddit",
                "comments",
                {"permalink": permalink},
                headers=request_headers,
                max_bytes=max_bytes,
            )
        state.consume(result, "https://old.reddit.com" + parsed.path)
        return result
    finally:
        state.close()
