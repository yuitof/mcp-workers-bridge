"""Private Cookie-backed X and Reddit operations over the Worker or directly."""

from email.message import Message
import json
import os
import re
import time
from urllib.parse import quote, unquote, urlencode, urlsplit

import requests
from requests.cookies import MockRequest, MockResponse

from .chrome_cookies import (
    ENV_NAMES,
    automatic_storage_state,
    normalize_profile,
    select_profile,
)
from .errors import BridgeError, ConfigurationError
from .paths import local_cache_root, vault_auth_path
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
X_ASSET_BASE = "https://abs.twimg.com/responsive-web/client-web/"


def _direct_timeout() -> float:
    raw = os.environ.get("WORKERS_BRIDGE_TIMEOUT_SECONDS", "50")
    try:
        value = float(raw)
    except ValueError:
        return 50.0
    return value if 1 <= value <= 600 else 50.0


def direct_target(
    service: str,
    operation: str,
    parameters: dict,
    query: str,
):
    if service == "x":
        if operation == "home":
            return "GET", "https://x.com/home"
        if operation == "graphql":
            return (
                "GET",
                "https://x.com/i/api/graphql/"
                f"{parameters['queryId']}/{parameters['operationName']}?{query}",
            )
        if operation == "asset":
            return "GET", X_ASSET_BASE + parameters["asset"]
    if service == "reddit":
        if operation == "identity":
            return "GET", "https://old.reddit.com/api/me.json"
        if operation == "listing":
            params = {"limit": "100", "raw_json": "1"}
            if parameters.get("after"):
                params["after"] = parameters["after"]
            return (
                "GET",
                "https://old.reddit.com/user/"
                f"{parameters['account']}/{parameters['collection']}.json?"
                f"{urlencode(params)}",
            )
        if operation == "comments":
            path = urlsplit(parameters["permalink"]).path.rstrip("/") + "/"
            return "GET", f"https://old.reddit.com{path}?context=0"
    if service == "youtube":
        if operation == "history":
            return "GET", "https://www.youtube.com/feed/history?hl=en"
        if operation == "browse":
            return (
                "POST",
                f"https://www.youtube.com/youtubei/v1/browse?{query}",
            )
    raise BridgeError("unsupported_operation", "Unsupported direct relay operation.")


class DirectRelay:
    def __init__(self, state=None):
        self.state = state

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        return None

    def relay(
        self,
        service: str,
        operation: str,
        parameters: dict,
        *,
        query: str = "",
        headers: dict[str, str] | None = None,
        body: str | None = None,
        max_bytes: int = 2_000_000,
    ):
        method, url = direct_target(service, operation, parameters, query)
        session = self.state.session if self.state is not None else requests.Session()
        request_headers = headers or {}
        prepared = session.prepare_request(
            requests.Request(
                method,
                url,
                headers=request_headers,
                data=body,
            )
        )
        try:
            response = session.send(
                prepared,
                stream=True,
                timeout=_direct_timeout(),
                allow_redirects=True,
            )
        except requests.RequestException:
            if self.state is None:
                session.close()
            raise BridgeError(
                "direct_transport_error",
                "Direct destination request failed.",
            ) from None
        try:
            raw = response.raw.read(max_bytes + 1, decode_content=True) or b""
            truncated = len(raw) > max_bytes
            raw = raw[:max_bytes]
            content_type = response.headers.get(
                "Content-Type", "application/octet-stream"
            )
            text = raw.decode("utf-8", errors="replace")
            result = {
                "ok": 200 <= response.status_code < 300,
                "bridge_ok": False,
                "transport": "direct",
                "service": service,
                "operation": operation,
                "upstream_http": response.status_code,
                "content_type": content_type,
                "body": text,
                "body_truncated": truncated,
                "_set_cookies": [],
            }
            if not result["ok"]:
                result["error"] = "upstream_http"
            if "json" in content_type.lower() and not truncated:
                try:
                    result["json"] = json.loads(text)
                except ValueError:
                    pass
            return result
        finally:
            response.close()
            if self.state is None:
                session.close()


def relay(
    config,
    state,
    service: str,
    operation: str,
    parameters: dict,
    *,
    query: str = "",
    headers: dict[str, str] | None = None,
    body: str | None = None,
    max_bytes: int = 2_000_000,
):
    if config is None:
        return DirectRelay(state).relay(
            service,
            operation,
            parameters,
            query=query,
            headers=headers,
            body=body,
            max_bytes=max_bytes,
        )
    with WorkersClient(config) as client:
        return client.relay(
            service,
            operation,
            parameters,
            query=query,
            headers=headers,
            body=body,
            max_bytes=max_bytes,
        )


class CookieState:
    def __init__(self, service: str, profile: str):
        if service not in SESSION_COOKIES:
            raise ValueError("Unknown social service.")
        profile = normalize_profile(profile)
        self.service = service
        self.session = requests.Session()
        self.session.auth = lambda request: request
        self.session.trust_env = False
        data = None
        raw = os.environ.get(ENV_NAMES[service])
        if raw:
            try:
                data = select_profile(json.loads(raw), service, profile)
            except ValueError:
                raise ConfigurationError(f"{ENV_NAMES[service]} is not valid JSON.") from None
        if data is None and profile == "Default":
            root_value = local_cache_root()
            path = vault_auth_path(root_value, service) if root_value else None
            if path is not None and path.is_file():
                try:
                    data = json.loads(path.read_text())
                except (OSError, ValueError):
                    raise ConfigurationError(
                        f"The private {service} storage-state file is invalid."
                    ) from None
        if data is None:
            data = automatic_storage_state(service, profile)
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


def x_home(
    config,
    headers: dict[str, str] | None = None,
    max_bytes: int = 2_000_000,
    *,
    profile: str,
):
    state = CookieState("x", profile)
    try:
        request_headers = state.headers(
            "https://x.com/home", _x_headers(headers)
        )
        result = relay(
            config,
            state,
            "x",
            "home",
            {},
            headers=request_headers,
            max_bytes=max_bytes,
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
    *,
    profile: str,
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
    state = CookieState("x", profile)
    try:
        request_headers = state.headers(url, _x_headers(headers))
        result = relay(
            config,
            state,
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
    result = relay(
        config,
        None,
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
    *,
    profile: str,
):
    state = CookieState("reddit", profile)
    try:
        url = "https://old.reddit.com/api/me.json"
        request_headers = state.headers(url, _reddit_headers(headers))
        result = relay(
            config,
            state,
            "reddit",
            "identity",
            {},
            headers=request_headers,
            max_bytes=max_bytes,
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
    *,
    profile: str,
):
    if not re.fullmatch(r"[A-Za-z0-9_-]{3,20}", account):
        raise BridgeError("invalid_argument", "Invalid Reddit account.")
    if collection not in {"upvoted", "saved", "downvoted", "hidden", "submitted", "comments"}:
        raise BridgeError("invalid_argument", "Invalid Reddit collection.")
    if after is not None and not re.fullmatch(r"t[13]_[a-z0-9]{1,16}", after):
        raise BridgeError("invalid_argument", "Invalid Reddit continuation cursor.")
    url = f"https://old.reddit.com/user/{account}/{collection}.json"
    state = CookieState("reddit", profile)
    try:
        request_headers = state.headers(url, _reddit_headers(headers))
        result = relay(
            config,
            state,
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
    *,
    profile: str,
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
    state = CookieState("reddit", profile)
    try:
        request_headers = state.headers(
            "https://old.reddit.com" + parsed.path, _reddit_headers(headers)
        )
        request_headers["Accept"] = "text/html"
        result = relay(
            config,
            state,
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
