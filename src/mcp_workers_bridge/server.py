"""Stdio MCP server for the service bridge transports."""

from typing import Any

from mcp.server.fastmcp import FastMCP

from .config import LegacyConfig, WorkersConfig
from .errors import BridgeError, ConfigurationError
from .legacy import LegacyBridge
from .workers import WorkersClient, error_result


mcp = FastMCP(
    "Workers Bridge",
    instructions=(
        "Use workers_bridge_probe before workers_youtube_captions. "
        "The Workers endpoint supports arbitrary public YouTube video IDs and uses "
        "signed-out requests. Caption text is omitted unless include_segments is true."
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
    legacy = LegacyConfig.from_env()
    return {
        "workers": {
            "configured": workers is not None,
            "url": workers.base_url if workers else None,
            "access_credentials_present": workers is not None,
            "configuration_error": worker_error,
        },
        "legacy": {
            "enabled": legacy.enabled,
            "vault_root": str(legacy.vault_root) if legacy.vault_root else None,
            "function": legacy.function,
            "table": legacy.table,
        },
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
    name="legacy_bridge_probe",
    description=(
        "Run the documented unsigned probe for the existing Lambda/Tailscale "
        "service-bridge through an existing vault checkout."
    ),
)
def legacy_bridge_probe() -> dict[str, Any]:
    try:
        return LegacyBridge(LegacyConfig.from_env()).probe()
    except BridgeError as error:
        return error_result(error)


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
