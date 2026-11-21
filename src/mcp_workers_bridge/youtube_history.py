"""Authenticated YouTube watch-history retrieval through Workers or directly."""

from __future__ import annotations

import copy
from datetime import date, datetime, timedelta
from email.message import Message
import hashlib
import json
import os
import re
import time
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests
from requests.cookies import MockRequest, MockResponse

from .chrome_cookies import (
    automatic_storage_state,
    normalize_profile,
    select_profile,
)
from .config import WorkersConfig
from .errors import BridgeError, ConfigurationError, UpstreamError
from .paths import local_cache_root, vault_auth_path
from .social import DirectRelay
from .workers import WorkersClient
from .youtube_cache import CacheManager, now


ORIGIN = "https://www.youtube.com"
HISTORY_URL = ORIGIN + "/feed/history?hl=en"
BROWSE_URL = ORIGIN + "/youtubei/v1/browse"
VIDEO_ID = re.compile(r"[-_A-Za-z0-9]{11}\Z")
DOMAINS = {"youtube.com", "www.youtube.com"}
SESSION_COOKIES = {"SAPISID", "__Secure-3PAPISID"}
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)
RETRY_STATUSES = {429, 500, 502, 503, 504}


class HistoryError(BridgeError):
    def __init__(self, message: str, code: str = "history_error"):
        super().__init__(code, message)


def record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return ""
    content = value.get("content")
    if isinstance(content, str):
        return content
    simple = value.get("simpleText")
    if isinstance(simple, str):
        return simple
    runs = value.get("runs")
    if not isinstance(runs, list):
        return ""
    return "".join(
        item.get("text", "")
        for item in runs
        if isinstance(item, dict) and isinstance(item.get("text"), str)
    )


def heading_date(label: str, today: date) -> date | None:
    label = label.strip()
    if label in ("Today", "Yesterday"):
        return today - timedelta(days=label == "Yesterday")
    weekdays = (
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
    )
    if label in weekdays:
        return today - timedelta(
            days=(today.weekday() - weekdays.index(label)) % 7 or 7
        )
    for pattern in ("%b %d, %Y", "%B %d, %Y", "%b %d", "%B %d"):
        try:
            parsed = datetime.strptime(label, pattern).date()
            if "%Y" not in pattern:
                parsed = parsed.replace(year=today.year)
                if parsed > today:
                    parsed = parsed.replace(year=today.year - 1)
            return parsed
        except ValueError:
            pass
    return None


def sections(payload: Any):
    if isinstance(payload, list):
        for value in payload:
            yield from sections(value)
    elif isinstance(payload, dict):
        if "itemSectionRenderer" in payload:
            section = payload["itemSectionRenderer"]
            if not isinstance(section, dict):
                return
            header = record(record(section.get("header")).get("itemSectionHeaderRenderer"))
            contents = section.get("contents")
            yield text(header.get("title")), contents if isinstance(contents, list) else []
        else:
            for value in payload.values():
                yield from sections(value)


def cards(payload: Any):
    if isinstance(payload, list):
        for value in payload:
            yield from cards(value)
    elif isinstance(payload, dict):
        if "videoRenderer" in payload:
            video = payload["videoRenderer"]
            if not isinstance(video, dict):
                return
            yield (
                video.get("videoId"),
                text(video.get("title")),
                text(video.get("ownerText") or video.get("shortBylineText")),
            )
        elif "lockupViewModel" in payload:
            video = payload["lockupViewModel"]
            if not isinstance(video, dict):
                return
            metadata = record(record(video.get("metadata")).get("lockupMetadataViewModel"))
            channel = ""
            rows = (
                record(record(metadata.get("metadata")).get("contentMetadataViewModel"))
                .get("metadataRows")
            )
            rows = rows if isinstance(rows, list) else []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                if (
                    record(row.get("lockupContentMetadataRowExtension")).get("contentType")
                    == "METADATA_ROW_CONTENT_TYPE_BYLINE"
                ):
                    parts = row.get("metadataParts")
                    first = parts[0] if isinstance(parts, list) and parts else {}
                    channel = text(first.get("text") if isinstance(first, dict) else "")
            yield video.get("contentId"), text(metadata.get("title")), channel
        elif "shortsLockupViewModel" in payload:
            video = payload["shortsLockupViewModel"]
            if not isinstance(video, dict):
                return
            watch_endpoint = record(
                record(record(video.get("onTap")).get("innertubeCommand")).get(
                    "reelWatchEndpoint"
                )
            )
            identifier = (
                watch_endpoint.get("videoId")
            )
            yield identifier, text(record(video.get("overlayMetadata")).get("primaryText")), ""
        elif "reelItemRenderer" in payload:
            video = payload["reelItemRenderer"]
            if not isinstance(video, dict):
                return
            yield video.get("videoId"), text(video.get("headline")), ""
        else:
            for value in payload.values():
                yield from cards(value)


def raw_video_ids(value: Any):
    if isinstance(value, list):
        for child in value:
            yield from raw_video_ids(child)
    elif isinstance(value, dict):
        for key, child in value.items():
            if key in ("videoId", "contentId") and isinstance(child, str) and VIDEO_ID.fullmatch(child):
                yield child
            else:
                yield from raw_video_ids(child)


def bootstrap_config(source: str) -> dict[str, Any]:
    config: dict[str, Any] = {}
    decoder = json.JSONDecoder()
    for match in re.finditer(r"ytcfg\.set\(\s*", source):
        try:
            value, _ = decoder.raw_decode(source[match.end() :])
        except ValueError:
            continue
        if isinstance(value, dict):
            config.update(value)
    if not config:
        raise HistoryError(
            "YouTube client configuration is missing; access may be blocked or awaiting consent."
        )
    if config.get("LOGGED_IN") is not True:
        raise HistoryError("YouTube session is expired or signed out.")
    context = config.get("INNERTUBE_CONTEXT")
    client = context.get("client") if isinstance(context, dict) else None
    if (
        not isinstance(client, dict)
        or not isinstance(client.get("clientVersion"), str)
        or not client["clientVersion"]
        or client.get("clientName") != "WEB"
    ):
        raise HistoryError("YouTube web client configuration is unrecognized.")
    return config


def history_items(payload: Any) -> list[dict[str, Any]]:
    try:
        if "contents" in payload:
            tabs = payload["contents"]["twoColumnBrowseResultsRenderer"]["tabs"]
            selected = [
                tab["tabRenderer"]
                for tab in tabs
                if tab.get("tabRenderer", {}).get("selected")
            ]
            if len(selected) != 1:
                raise HistoryError("YouTube history tab is ambiguous.")
            items = selected[0]["content"]["sectionListRenderer"]["contents"]
        else:
            batches = []
            for key in (
                "onResponseReceivedActions",
                "onResponseReceivedEndpoints",
                "onResponseReceivedCommands",
            ):
                for action in payload.get(key, []):
                    for name in (
                        "appendContinuationItemsAction",
                        "reloadContinuationItemsCommand",
                    ):
                        if name in action:
                            batches.append(action[name]["continuationItems"])
            if len(batches) != 1:
                raise HistoryError("YouTube history continuation body is missing or ambiguous.")
            items = batches[0]
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            raise TypeError
        return items
    except (KeyError, TypeError, AttributeError):
        raise HistoryError("YouTube history response structure is unrecognized.") from None


def continuation_token(items: list[dict[str, Any]]) -> str | None:
    tokens: set[str] = set()

    def walk(value: Any):
        if isinstance(value, list):
            for child in value:
                walk(child)
        elif isinstance(value, dict):
            if "continuationItemRenderer" in value:
                endpoint = value["continuationItemRenderer"].get(
                    "continuationEndpoint", {}
                )
                path = (
                    endpoint.get("commandMetadata", {})
                    .get("webCommandMetadata", {})
                    .get("apiUrl", "/youtubei/v1/browse")
                )
                token = endpoint.get("continuationCommand", {}).get("token")
                if path != "/youtubei/v1/browse" or not isinstance(token, str) or not token:
                    raise HistoryError(
                        "YouTube history continuation endpoint is unrecognized."
                    )
                tokens.add(token)
            elif "continuationItemView" in value:
                raise HistoryError("YouTube history continuation format is unrecognized.")
            else:
                for key, child in value.items():
                    if key not in ("header", "menu", "secondaryContents"):
                        walk(child)

    try:
        walk(items)
    except (AttributeError, TypeError):
        raise HistoryError(
            "YouTube history continuation structure is unrecognized."
        ) from None
    if len(tokens) > 1:
        raise HistoryError("YouTube history continuation tokens are ambiguous.")
    return next(iter(tokens), None)


def sapisidhash(sapisid: str, stamp: int | None = None) -> str:
    timestamp = str(int(time.time()) if stamp is None else stamp)
    digest = hashlib.sha1(
        f"{timestamp} {sapisid} {ORIGIN}".encode()
    ).hexdigest()
    return f"SAPISIDHASH {timestamp}_{digest}"


def _state_data(profile: str) -> dict[str, Any]:
    profile = normalize_profile(profile)
    data = None
    raw = os.environ.get("YOUTUBE_STORAGE_STATE_JSON")
    if raw:
        try:
            data = select_profile(json.loads(raw), "youtube", profile)
        except ValueError:
            raise ConfigurationError(
                "YOUTUBE_STORAGE_STATE_JSON is not valid JSON."
            ) from None
    if data is None and profile == "Default":
        root_value = local_cache_root()
        path = vault_auth_path(root_value, "youtube") if root_value else None
        if path is not None and path.is_file():
            try:
                data = json.loads(path.read_text())
            except (OSError, ValueError):
                raise ConfigurationError(
                    "The private YouTube storage-state file is invalid."
                ) from None
    if data is None:
        data = automatic_storage_state("youtube", profile)
    if not isinstance(data, dict) or not isinstance(data.get("cookies"), list):
        raise ConfigurationError("The private YouTube storage state is invalid.")
    return data


class YouTubeCookieState:
    def __init__(self, profile: str):
        data = _state_data(profile)
        now = time.time()
        self.session = requests.Session()
        self.session.auth = lambda request: request
        self.session.trust_env = False
        cookies = []
        for cookie in data["cookies"]:
            if not isinstance(cookie, dict):
                continue
            domain = cookie.get("domain")
            name = cookie.get("name")
            value = cookie.get("value")
            expires = cookie.get("expires", -1)
            if (
                not isinstance(domain, str)
                or domain.lstrip(".") not in DOMAINS
                or not isinstance(name, str)
                or not isinstance(value, str)
                or isinstance(expires, bool)
                or not isinstance(expires, (int, float))
                or (expires > 0 and expires <= now)
            ):
                continue
            cookies.append(cookie)
        if not SESSION_COOKIES & {cookie["name"] for cookie in cookies}:
            raise ConfigurationError(
                "The private YouTube session is missing required unexpired cookies."
            )
        for cookie in cookies:
            expires = cookie.get("expires", -1)
            self.session.cookies.set(
                cookie["name"],
                cookie["value"],
                domain=cookie["domain"],
                path=cookie.get("path") or "/",
                secure=bool(cookie.get("secure", True)),
                expires=None if not isinstance(expires, (int, float)) or expires <= 0 else int(expires),
            )

    def headers(self, method: str, url: str, extra: dict[str, str]) -> dict[str, str]:
        prepared = self.session.prepare_request(
            requests.Request(method, url, headers=extra)
        )
        if prepared.headers.get("Cookie"):
            extra["Cookie"] = prepared.headers["Cookie"]
        allowed = {
            "accept",
            "accept-language",
            "authorization",
            "content-type",
            "cookie",
            "origin",
            "referer",
            "user-agent",
            "x-goog-authuser",
            "x-goog-pageid",
            "x-goog-visitor-id",
            "x-youtube-client-name",
            "x-youtube-client-version",
        }
        return {
            key: value for key, value in extra.items() if key.lower() in allowed
        }

    def cookie_header(self, method: str, url: str) -> str:
        return self.headers(method, url, {}).get("Cookie", "")

    def consume(self, response: dict[str, Any], url: str) -> None:
        encoded = response.pop("_set_cookies", [])
        if not encoded:
            return
        prepared = self.session.prepare_request(requests.Request("GET", url))
        message = Message()
        for value in encoded:
            message.add_header("Set-Cookie", value)
        self.session.cookies.extract_cookies(
            MockResponse(message), MockRequest(prepared)
        )

    def close(self):
        self.session.close()


def relay_history(
    client,
    state: YouTubeCookieState,
    operation: str,
    *,
    query: str,
    headers: dict[str, str],
    body: str | None,
    max_bytes: int,
) -> str:
    stage = "history" if operation == "history" else "browse"
    url = HISTORY_URL if operation == "history" else BROWSE_URL
    status = None
    for attempt in range(3):
        result = client.relay(
            "youtube",
            operation,
            {},
            query=query,
            headers=headers,
            body=body,
            max_bytes=max_bytes,
        )
        state.consume(result, url)
        if result.get("body_truncated"):
            raise HistoryError(f"YouTube {stage} response exceeded the supported size.")
        status = result.get("upstream_http")
        if result.get("ok") is True:
            return result.get("body", "")
        if status not in RETRY_STATUSES or attempt == 2:
            if isinstance(status, int):
                raise UpstreamError(stage, status)
            raise HistoryError(f"YouTube {stage} request failed.")
        time.sleep(0.5 * 2**attempt)
    raise UpstreamError(stage, status if isinstance(status, int) else 0)


def _checkpoint_body(
    context: dict[str, Any], continuation: str | None
) -> str:
    payload = {
        "context": context,
        **(
            {"continuation": continuation}
            if continuation
            else {"browseId": "FEhistory"}
        ),
    }
    return json.dumps(payload, separators=(",", ":"))


def _browse_headers(
    state: YouTubeCookieState,
    config: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, str]:
    client = context.get("client", {})
    authuser = str(config.get("SESSION_INDEX", 0))
    client_name = str(config.get("INNERTUBE_CONTEXT_CLIENT_NAME", 1))
    visitor = client.get("visitorData", "")
    if not authuser.isdigit() or not client_name.isdigit() or not isinstance(visitor, str):
        raise HistoryError("YouTube client session metadata is invalid.")
    headers = {
        "User-Agent": USER_AGENT,
        "Accept-Language": "en-GB,en;q=0.9",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": ORIGIN,
        "X-Origin": ORIGIN,
        "Referer": HISTORY_URL,
        "X-Goog-Authuser": authuser,
        "X-Youtube-Client-Name": client_name,
        "X-Youtube-Client-Version": str(client["clientVersion"]),
        "X-Goog-Visitor-Id": visitor,
    }
    delegated = config.get("DELEGATED_SESSION_ID")
    if delegated:
        if not isinstance(delegated, str) or re.search(r"[\r\n]", delegated):
            raise HistoryError("YouTube delegated session metadata is invalid.")
        headers["X-Goog-PageId"] = delegated
    cookie_header = state.cookie_header("POST", BROWSE_URL)
    cookies = dict(
        re.findall(r"(?:^|;\s*)([^=;]+)=([^;]*)", cookie_header)
    )
    sapisid = cookies.get("SAPISID") or cookies.get("__Secure-3PAPISID")
    if not sapisid:
        raise HistoryError("YouTube authorization cookie is missing or expired.")
    headers["Authorization"] = sapisidhash(sapisid)
    return state.headers("POST", BROWSE_URL, headers)


def _parse_browse_page(source: str) -> tuple[list[dict[str, Any]], str | None]:
    try:
        payload = json.loads(source)
    except ValueError:
        raise HistoryError("YouTube history API returned invalid JSON.") from None
    if not isinstance(payload, dict) or payload.get("error"):
        raise HistoryError("YouTube history API returned an error or invalid response.")
    response_context = payload.get("responseContext")
    main_context = (
        response_context.get("mainAppWebResponseContext")
        if isinstance(response_context, dict)
        else None
    )
    if not isinstance(main_context, dict) or main_context.get("loggedOut") is not False:
        raise HistoryError(
            "YouTube history authentication could not be verified; renew the private session."
        )
    items = history_items(payload)
    return items, continuation_token(items)


def _resolve_cutoff(
    since: str | None, timezone_name: str, today: date
) -> date:
    if since is None:
        return today - timedelta(days=30)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", since):
        raise HistoryError(
            "since must be an ISO date in YYYY-MM-DD format.",
            code="invalid_argument",
        )
    try:
        cutoff = date.fromisoformat(since)
    except ValueError:
        raise HistoryError(
            "since must be a valid ISO date.",
            code="invalid_argument",
        ) from None
    if cutoff > today:
        raise HistoryError(
            f"since cannot be later than {today} in {timezone_name}.",
            code="invalid_argument",
        )
    return cutoff


def merge_observations(
    index: dict[str, Any], observations: dict[tuple[str, str], dict[str, Any]]
) -> dict[str, Any]:
    result = copy.deepcopy(index)
    videos = {video["video_id"]: video for video in result.get("videos", [])}
    for item in observations.values():
        identifier = item["video_id"]
        if not VIDEO_ID.fullmatch(identifier):
            raise HistoryError("YouTube history returned an invalid video ID.")
        video = videos.setdefault(
            identifier,
            {
                "video_id": identifier,
                "title": "",
                "channel": None,
                "url": f"https://www.youtube.com/watch?v={identifier}",
                "watched_at": [],
                "watched_on": [],
                "history_sources": [],
                "transcript": {"status": "unchecked"},
            },
        )
        for key in ("title", "channel"):
            if item.get(key):
                video[key] = item[key]
        video["watched_on"] = sorted(
            set(video.get("watched_on", [])) | {item["watched_on"]}
        )
        source = "YouTube account watch-history feed"
        if source not in video.setdefault("history_sources", []):
            video["history_sources"].append(source)
    result["videos"] = sorted(
        videos.values(),
        key=lambda video: max(
            video.get("watched_on", []) + video.get("watched_at", []),
            default="",
        ),
        reverse=True,
    )
    return result


def coverage_for(
    *,
    cutoff: date,
    ended: date,
    timezone_name: str,
    complete: bool,
    oldest: date | None,
    unknown: set[str],
    failures: list[str],
    pages: int,
    reason: str,
    retrieval_method: str = "Cloudflare Workers InnerTube relay",
) -> dict[str, Any]:
    return {
        "source_url": "https://www.youtube.com/feed/history",
        "timezone": timezone_name,
        "retrieval_method": retrieval_method,
        "coverage_start_date": str(cutoff),
        "coverage_end_date": str(ended),
        "updated_at": now(),
        "range_complete": complete,
        "oldest_loaded_date": str(oldest) if oldest else None,
        "date_resolution": "day",
        "unparsed_headings": sorted(unknown),
        "errors": list(failures),
        "pages_fetched": pages,
        "termination_reason": reason,
        "coverage_note": (
            "Account history exposes day headings; exact watch times and repeated "
            "watches within a day are not exposed."
        ),
    }


def videos_in_range(
    index: dict[str, Any], cutoff: date, ended: date
) -> list[dict[str, Any]]:
    start, finish = str(cutoff), str(ended)
    selected = []
    for video in index.get("videos", []):
        watched = {
            day
            for day in video.get("watched_on", [])
            if isinstance(day, str) and start <= day[:10] <= finish
        }
        watched.update(
            day[:10]
            for day in video.get("watched_at", [])
            if isinstance(day, str) and start <= day[:10] <= finish
        )
        if not watched:
            continue
        selected.append(
            {
                "video_id": video["video_id"],
                "title": video.get("title", ""),
                "channel": video.get("channel"),
                "url": video.get(
                    "url", f"https://www.youtube.com/watch?v={video['video_id']}"
                ),
                "watched_on": sorted(watched, reverse=True),
                "transcript_status": video.get("transcript", {}).get(
                    "status", "unchecked"
                ),
            }
        )
    return sorted(
        selected,
        key=lambda video: max(video["watched_on"], default=""),
        reverse=True,
    )


def history_result(
    index: dict[str, Any],
    coverage: dict[str, Any],
    manager: CacheManager,
    *,
    refresh: bool,
) -> dict[str, Any]:
    cutoff = date.fromisoformat(coverage["coverage_start_date"])
    ended = date.fromisoformat(coverage["coverage_end_date"])
    videos = videos_in_range(index, cutoff, ended)
    complete = coverage.get("range_complete") is True
    result: dict[str, Any] = {
        "ok": complete,
        "partial": not complete,
        "refresh": refresh,
        "cache_only": not refresh,
        "video_count": len(videos),
        "videos": videos,
        "coverage": coverage,
        "cache": manager.describe(),
    }
    if not complete:
        result.update(
            error="history_incomplete",
            message=(
                "YouTube history coverage is incomplete; use the returned results "
                "and retry later."
            ),
        )
    return result


def youtube_history(
    config: WorkersConfig | None,
    since: str | None = None,
    max_pages: int = 100,
    timezone_name: str | None = None,
    *,
    refresh: bool = True,
    cache_enabled: bool = True,
    cache_manager: CacheManager | None = None,
    profile: str | None = None,
) -> dict[str, Any]:
    if not 1 <= max_pages <= 500:
        raise HistoryError(
            "max_pages must be between 1 and 500.",
            code="invalid_argument",
        )
    timezone_name = timezone_name or os.environ.get(
        "YOUTUBE_TIMEZONE", "Europe/London"
    )
    try:
        zone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError):
        raise HistoryError(
            "timezone must be a valid IANA time-zone name.",
            code="invalid_argument",
        ) from None
    started = datetime.now(zone)
    manager = cache_manager or CacheManager.from_env(cache_enabled)
    index = manager.load()
    prior = index.get("metadata", {}).get("cloud_history", {})
    cutoff = _resolve_cutoff(since, timezone_name, started.date())
    if since is None and isinstance(prior, dict):
        if (
            prior.get("range_complete") is True
            and prior.get("timezone") == timezone_name
            and isinstance(prior.get("coverage_end_date"), str)
        ):
            try:
                cutoff = date.fromisoformat(prior["coverage_end_date"])
            except ValueError:
                pass
    if cutoff > started.date():
        raise HistoryError(
            f"since cannot be later than {started.date()} in {timezone_name}.",
            code="invalid_argument",
        )
    if not refresh:
        cached_coverage = (
            copy.deepcopy(prior)
            if isinstance(prior, dict) and prior
            else coverage_for(
                cutoff=cutoff,
                ended=started.date(),
                timezone_name=timezone_name,
                complete=False,
                oldest=None,
                unknown={"(history cache is empty)"},
                failures=["No cached history coverage is available."],
                pages=0,
                reason="cache_miss",
            )
        )
        return history_result(
            index,
            cached_coverage,
            manager,
            refresh=False,
        )
    if profile is None:
        raise ConfigurationError(
            "profile is required when refresh=true; use Default or Profile N."
        )
    state = YouTubeCookieState(profile)
    observations: dict[tuple[str, str], dict[str, Any]] = {}
    unknown: set[str] = set()
    failures: list[str] = []
    pages = 0
    oldest: date | None = None
    token: str | None = None
    complete = False
    reason = "page_cap"
    seen: set[str] = set()

    def checkpoint() -> dict[str, Any]:
        nonlocal index
        index = merge_observations(index, observations)
        coverage = coverage_for(
            cutoff=cutoff,
            ended=started.date(),
            timezone_name=timezone_name,
            complete=complete,
            oldest=oldest,
            unknown=unknown,
            failures=failures,
            pages=pages,
            reason=reason,
        )
        index["metadata"]["cloud_history"] = copy.deepcopy(coverage)
        index["metadata"]["updated_at"] = now()
        index = manager.save(index)
        return coverage

    def consume(items: list[dict[str, Any]]) -> None:
        nonlocal oldest
        for item in items:
            if not any(
                key in item
                for key in ("itemSectionRenderer", "continuationItemRenderer")
            ):
                unknown.add("(unrecognized history item)")
        for label, contents in sections(items):
            records = list(cards(contents))
            if set(raw_video_ids(contents)) - {record[0] for record in records}:
                unknown.add("(unrecognized history card)")
            day = heading_date(label, started.date())
            if day is None:
                if records:
                    unknown.add(label or "(missing date heading)")
                continue
            oldest = min(oldest, day) if oldest else day
            if day < cutoff:
                continue
            for identifier, title, channel in records:
                if (
                    not isinstance(identifier, str)
                    or not VIDEO_ID.fullmatch(identifier)
                    or not title
                ):
                    unknown.add("(unrecognized history card)")
                    continue
                observations[(identifier, str(day))] = {
                    "video_id": identifier,
                    "title": title,
                    "channel": channel or None,
                    "watched_on": str(day),
                }

    try:
        transport = (
            WorkersClient(config) if config is not None else DirectRelay(state)
        )
        with transport as client:
            bootstrap_headers = state.headers(
                "GET",
                HISTORY_URL,
                {
                    "User-Agent": USER_AGENT,
                    "Accept-Language": "en-GB,en;q=0.9",
                    "Accept": "text/html,application/xhtml+xml",
                },
            )
            bootstrap_source = relay_history(
                client,
                state,
                "history",
                query="",
                headers=bootstrap_headers,
                body=None,
                max_bytes=8_000_000,
            )
            bootstrap = bootstrap_config(bootstrap_source)
            context = copy.deepcopy(bootstrap["INNERTUBE_CONTEXT"])
            client_context = context["client"]
            client_context.update(
                hl="en",
                timeZone=timezone_name,
                utcOffsetMinutes=int(
                    datetime.now(zone).utcoffset().total_seconds() / 60
                ),
            )
            headers = _browse_headers(state, bootstrap, context)
            api_key = bootstrap.get("INNERTUBE_API_KEY")
            if api_key is not None and (
                not isinstance(api_key, str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", api_key)
            ):
                raise HistoryError("YouTube API key metadata is invalid.")

            for _ in range(max_pages):
                query_data = {"prettyPrint": "false"}
                if api_key:
                    query_data["key"] = api_key
                query = urlencode(query_data)
                try:
                    source = relay_history(
                        client,
                        state,
                        "browse",
                        query=query,
                        headers=headers,
                        body=_checkpoint_body(context, token),
                        max_bytes=12_000_000,
                    )
                    items, next_token = _parse_browse_page(source)
                    consume(items)
                    pages += 1
                except BridgeError as error:
                    if pages == 0:
                        if not index.get("videos"):
                            raise
                        failures.append(str(error))
                        reason = "request_failed"
                        coverage = checkpoint()
                        break
                    failures.append(str(error))
                    reason = "request_failed"
                    coverage = checkpoint()
                    break

                token = next_token
                if oldest and oldest < cutoff:
                    reason = "older_than_cutoff"
                    complete = not unknown
                    coverage = checkpoint()
                    break
                if token is None:
                    reason = "feed_exhausted"
                    complete = not unknown
                    coverage = checkpoint()
                    break
                if token in seen:
                    reason = "repeated_continuation"
                    coverage = checkpoint()
                    break
                seen.add(token)
                coverage = checkpoint()
    finally:
        state.close()

    coverage = checkpoint()
    result = history_result(index, coverage, manager, refresh=True)
    result["observation_count"] = len(observations)
    return result
