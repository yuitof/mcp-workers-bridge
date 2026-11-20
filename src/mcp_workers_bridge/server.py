"""Stdio MCP server for the service bridge transports."""

from typing import Any

from mcp.server.fastmcp import FastMCP

from .config import WorkersConfig
from .errors import BridgeError, ConfigurationError
from .social import (
    reddit_comments,
    reddit_identity,
    reddit_listing,
    x_asset,
    x_graphql,
    x_home,
)
from .youtube_history import youtube_history
from .youtube_cache import CacheManager
from .workers import WorkersClient, error_result
from .x_collections import collect_x_collection


mcp = FastMCP(
    "Workers Bridge",
    instructions=(
        "Use workers_bridge_probe before authenticated or public network reads. "
        "The Workers endpoint supports arbitrary public YouTube video IDs and uses "
        "signed-out caption requests. Caption text is omitted unless include_segments "
        "is true. workers_youtube_history uses only the authenticated YouTube account "
        "session through Workers and reports incomplete pagination as partial "
        "coverage. It can read the vault-compatible local/S3 cache; cached reads "
        "set refresh=false."
    ),
)


def _workers():
    config = WorkersConfig.from_env()
    if config is None:
        raise ConfigurationError("Cloudflare Workers bridge is not configured.")
    return config


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
    try:
        with WorkersClient(_workers()) as client:
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
        with WorkersClient(_workers()) as client:
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
    headers: dict[str, str] | None = None,
    max_bytes: int = 2_000_000,
) -> dict[str, Any]:
    try:
        return x_home(_workers(), headers, max_bytes)
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
    max_pages: int = 20,
    cursor: str | None = None,
    count: int = 100,
) -> dict[str, Any]:
    return _workers_x_collection("likes", max_pages, cursor, count)


@mcp.tool(
    name="workers_x_bookmarks",
    description=(
        "Discover the current X GraphQL operation and transaction headers, verify "
        "the Viewer identity, and collect authenticated Bookmarks with cursor pagination."
    ),
)
def workers_x_bookmarks(
    max_pages: int = 20,
    cursor: str | None = None,
    count: int = 100,
) -> dict[str, Any]:
    return _workers_x_collection("bookmarks", max_pages, cursor, count)


@mcp.tool(
    name="workers_reddit_identity",
    description=(
        "Verify and return the authenticated Reddit identity through the Cloudflare "
        "Workers relay."
    ),
)
def workers_reddit_identity(
    headers: dict[str, str] | None = None,
    max_bytes: int = 1_000_000,
) -> dict[str, Any]:
    try:
        return reddit_identity(_workers(), headers, max_bytes)
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
    account: str,
    collection: str,
    after: str | None = None,
    headers: dict[str, str] | None = None,
    max_bytes: int = 8_000_000,
) -> dict[str, Any]:
    try:
        return reddit_listing(
            _workers(), account, collection, after, headers, max_bytes
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
    permalink: str,
    headers: dict[str, str] | None = None,
    max_bytes: int = 12_000_000,
) -> dict[str, Any]:
    try:
        return reddit_comments(_workers(), permalink, headers, max_bytes)
    except BridgeError as error:
        return error_result(error)


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
