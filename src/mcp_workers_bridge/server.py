"""Stdio MCP server for the service bridge transports."""

import os
from typing import Any

from mcp.server import MCPServer

from .captions import direct_captions
from .chrome_cookies import clear_storage_state, normalize_profile
from .config import WorkersConfig
from .errors import BridgeError, ConfigurationError
from .paths import local_cache_root, vault_cache_root
from .social import (
    reddit_comments,
    reddit_identity,
    reddit_listing,
    x_asset,
    x_graphql,
    x_home,
)
from .youtube_history import youtube_history
from .youtube_cache import CacheManager, S3Cache, clear_local_cache
from .workers import WorkersClient, error_result
from .x_collections import collect_x_collection


mcp = MCPServer(
    "Workers Bridge",
    instructions=(
        "Use workers_bridge_probe before authenticated or public network reads. "
        "The Workers endpoint supports arbitrary public YouTube video IDs and uses "
        "signed-out caption requests. Caption text is omitted unless include_segments "
        "is true. workers_youtube_history uses only the authenticated YouTube account "
        "session through Workers and reports incomplete pagination as partial "
        "coverage. It can read the vault-compatible local/S3 cache; cached reads "
        "set refresh=false. When SERVICE_BRIDGE_WORKER_URL is unset, tools use "
        "direct destination connections instead of the Workers relay."
    ),
)


def _workers():
    return WorkersConfig.from_env()


@mcp.tool(
    name="bridge_configuration",
    description="Report configured bridge backends without exposing secret values.",
)
def bridge_configuration() -> dict[str, Any]:
    try:
        workers = WorkersConfig.from_env()
        worker_error = None
    except ConfigurationError as error:
        workers = None
        worker_error = str(error)
    try:
        cache_manager = CacheManager.from_env()
        cache = {
            "enabled": cache_manager.enabled,
            "directory": str(cache_manager.root) if cache_manager.root else None,
            "s3_configured": cache_manager.s3_configured,
            "error": None,
        }
    except BridgeError as error:
        cache = {
            "enabled": False,
            "directory": None,
            "s3_configured": False,
            "error": str(error),
        }
    return {
        "mode": "workers" if workers is not None else "direct",
        "workers": {
            "configured": workers is not None,
            "url": workers.base_url if workers else None,
            "access_credentials_present": workers is not None,
            "configuration_error": worker_error,
        },
        "youtube_cache": cache,
    }


@mcp.tool(
    name="workers_bridge_probe",
    description=(
        "Verify Cloudflare Access and a Gateway public-IP observation through "
        "the Cloudflare Workers bridge."
    ),
)
def workers_bridge_probe() -> dict[str, Any]:
    config = _workers()
    if config is None:
        return {
            "ok": False,
            "error": "workers_not_configured",
            "mode": "direct",
            "message": (
                "Cloudflare Workers bridge is not configured; direct transport "
                "is active."
            ),
        }
    try:
        with WorkersClient(config) as client:
            return client.probe()
    except BridgeError as error:
        return error_result(error)


@mcp.tool(
    name="workers_youtube_captions",
    description=(
        "Retrieve signed-out English captions for any public YouTube video through "
        "the Cloudflare Workers bridge. Manual captions are preferred, with automatic "
        "captions used when no manual track exists."
    ),
)
def workers_youtube_captions(
    video_id: str,
    include_segments: bool = False,
    max_segments: int = 5000,
) -> dict[str, Any]:
    if not 1 <= max_segments <= 20_000:
        return {
            "ok": False,
            "error": "invalid_argument",
            "message": "max_segments must be between 1 and 20000.",
        }
    try:
        config = _workers()
        if config is None:
            return direct_captions(video_id, include_segments, max_segments)
        with WorkersClient(config) as client:
            return client.captions(video_id, include_segments, max_segments)
    except BridgeError as error:
        return error_result(error)


@mcp.tool(
    name="workers_youtube_history",
    description=(
        "Retrieve authenticated YouTube account watch history through Workers "
        "with vault-compatible local/S3 caching."
    ),
)
def workers_youtube_history(
    profile: str,
    since: str | None = None,
    max_pages: int = 100,
    timezone: str | None = None,
    use_cache: bool = True,
    refresh: bool = True,
) -> dict[str, Any]:
    if not 1 <= max_pages <= 500:
        return {
            "ok": False,
            "error": "invalid_argument",
            "message": "max_pages must be between 1 and 500.",
        }
    try:
        return youtube_history(
            _workers() if refresh else None,
            since,
            max_pages,
            timezone,
            refresh=refresh,
            cache_enabled=use_cache,
            profile=profile,
        )
    except BridgeError as error:
        return error_result(error)


@mcp.tool(
    name="workers_x_home",
    description=(
        "Fetch the authenticated X home page through the Cloudflare Workers relay."
    ),
)
def workers_x_home(
    profile: str,
    headers: dict[str, str] | None = None,
    max_bytes: int = 2_000_000,
) -> dict[str, Any]:
    try:
        return x_home(_workers(), headers, max_bytes, profile=profile)
    except BridgeError as error:
        return error_result(error)


@mcp.tool(
    name="workers_x_graphql",
    description=(
        "Perform an allowlisted read-only X GraphQL request through the Cloudflare "
        "Workers relay. Authentication and transaction headers must be supplied."
    ),
)
def workers_x_graphql(
    profile: str,
    query_id: str,
    operation_name: str,
    variables: dict[str, Any],
    features: dict[str, Any],
    field_toggles: dict[str, Any],
    headers: dict[str, str] | None = None,
    max_bytes: int = 4_000_000,
) -> dict[str, Any]:
    try:
        return x_graphql(
            _workers(),
            query_id,
            operation_name,
            variables,
            features,
            field_toggles,
            headers,
            max_bytes,
            profile=profile,
        )
    except BridgeError as error:
        return error_result(error)


@mcp.tool(
    name="workers_x_asset",
    description=(
        "Fetch an allowlisted X web-client JavaScript asset through the Cloudflare "
        "Workers relay without sending X cookies to the asset host."
    ),
)
def workers_x_asset(
    asset: str,
    headers: dict[str, str] | None = None,
    max_bytes: int = 4_000_000,
) -> dict[str, Any]:
    try:
        return x_asset(_workers(), asset, headers, max_bytes)
    except BridgeError as error:
        return error_result(error)


def _workers_x_collection(
    collection: str,
    max_pages: int,
    cursor: str | None,
    count: int,
    profile: str,
) -> dict[str, Any]:
    if not 1 <= max_pages <= 200:
        return {
            "ok": False,
            "error": "invalid_argument",
            "message": "max_pages must be between 1 and 200.",
        }
    if not 1 <= count <= 100:
        return {
            "ok": False,
            "error": "invalid_argument",
            "message": "count must be between 1 and 100.",
        }
    try:
        return collect_x_collection(
            _workers(),
            collection,
            max_pages=max_pages,
            cursor=cursor,
            count=count,
            profile=profile,
        )
    except BridgeError as error:
        return error_result(error)


@mcp.tool(
    name="workers_x_likes",
    description=(
        "Discover the current X GraphQL operation and transaction headers, verify "
        "the Viewer identity, and collect authenticated Likes with cursor pagination."
    ),
)
def workers_x_likes(
    profile: str,
    max_pages: int = 20,
    cursor: str | None = None,
    count: int = 100,
) -> dict[str, Any]:
    return _workers_x_collection("likes", max_pages, cursor, count, profile)


@mcp.tool(
    name="workers_x_bookmarks",
    description=(
        "Discover the current X GraphQL operation and transaction headers, verify "
        "the Viewer identity, and collect authenticated Bookmarks with cursor pagination."
    ),
)
def workers_x_bookmarks(
    profile: str,
    max_pages: int = 20,
    cursor: str | None = None,
    count: int = 100,
) -> dict[str, Any]:
    return _workers_x_collection("bookmarks", max_pages, cursor, count, profile)


@mcp.tool(
    name="workers_reddit_identity",
    description=(
        "Verify and return the authenticated Reddit identity through the Cloudflare "
        "Workers relay."
    ),
)
def workers_reddit_identity(
    profile: str,
    headers: dict[str, str] | None = None,
    max_bytes: int = 1_000_000,
) -> dict[str, Any]:
    try:
        return reddit_identity(_workers(), headers, max_bytes, profile=profile)
    except BridgeError as error:
        return error_result(error)


@mcp.tool(
    name="workers_reddit_listing",
    description=(
        "Fetch one authenticated Old Reddit JSON listing page for an allowlisted "
        "collection through the Cloudflare Workers relay."
    ),
)
def workers_reddit_listing(
    profile: str,
    account: str,
    collection: str,
    after: str | None = None,
    headers: dict[str, str] | None = None,
    max_bytes: int = 8_000_000,
) -> dict[str, Any]:
    try:
        return reddit_listing(
            _workers(),
            account,
            collection,
            after,
            headers,
            max_bytes,
            profile=profile,
        )
    except BridgeError as error:
        return error_result(error)


@mcp.tool(
    name="workers_reddit_comments",
    description=(
        "Fetch an authenticated Old Reddit post/comment page through the Cloudflare "
        "Workers relay."
    ),
)
def workers_reddit_comments(
    profile: str,
    permalink: str,
    headers: dict[str, str] | None = None,
    max_bytes: int = 12_000_000,
) -> dict[str, Any]:
    try:
        return reddit_comments(
            _workers(),
            permalink,
            headers,
            max_bytes,
            profile=profile,
        )
    except BridgeError as error:
        return error_result(error)


@mcp.tool(
    name="clear_cookie_state",
    description=(
        "Clear cookie state from the running MCP process, optionally limited to "
        "one service and profile. The parent Codex environment is not modified."
    ),
)
def clear_cookie_state(
    service: str | None = None,
    profile: str | None = None,
) -> dict[str, Any]:
    services = ["youtube", "x", "reddit"]
    if service is not None and service not in services:
        return {
            "ok": False,
            "error": "invalid_argument",
            "message": "service must be youtube, x, or reddit.",
        }
    try:
        selected = normalize_profile(profile) if profile is not None else None
        cleared = {
            name: clear_storage_state(name, selected)
            for name in ([service] if service is not None else services)
        }
        return {
            "ok": True,
            "cleared": {
                name: profiles for name, profiles in cleared.items() if profiles
            },
            "process_local": True,
            "message": (
                "Cleared from the MCP process. Restarting Codex restores values "
                "inherited from the parent environment."
            ),
        }
    except BridgeError as error:
        return error_result(error)


@mcp.tool(
    name="clear_cache",
    description=(
        "Delete the YouTube cache locally, from S3, or both. Local clearing "
        "removes only history.json and transcripts/, never auth cookies."
    ),
)
def clear_cache(
    target: str = "both",
    service: str = "youtube",
) -> dict[str, Any]:
    if service != "youtube":
        return {
            "ok": False,
            "error": "invalid_argument",
            "message": "service must be youtube.",
        }
    if target not in {"local", "s3", "both"}:
        return {
            "ok": False,
            "error": "invalid_argument",
            "message": "target must be local, s3, or both.",
        }
    result: dict[str, Any] = {"ok": True, "service": service, "target": target}
    try:
        if target in {"local", "both"}:
            root_value = local_cache_root()
            if root_value:
                result["local"] = clear_local_cache(
                    vault_cache_root(root_value, service)
                )
            else:
                result["local"] = {"skipped": "LOCAL_CACHE_ROOT is not set."}
        if target in {"s3", "both"}:
            bucket = os.environ.get("S3_CACHE_BUCKET", "").strip()
            if bucket:
                result["s3"] = {"removed_objects": S3Cache(bucket).clear()}
            else:
                result["s3"] = {"skipped": "S3_CACHE_BUCKET is not set."}
        return result
    except BridgeError as error:
        return error_result(error)
    except Exception:
        return {
            "ok": False,
            "error": "cache_clear_failed",
            "message": "The cache could not be cleared.",
        }


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
