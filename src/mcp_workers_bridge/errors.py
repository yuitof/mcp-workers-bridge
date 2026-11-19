"""Credential-safe error categories exposed through MCP tools."""


class BridgeError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class ConfigurationError(BridgeError):
    def __init__(self, message: str):
        super().__init__("configuration_error", message)


class AccessDeniedError(BridgeError):
    def __init__(self, status: int):
        super().__init__(
            "access_denied",
            f"Cloudflare Access returned HTTP {status} before the Worker ran.",
        )


class TransportError(BridgeError):
    def __init__(self, message: str):
        super().__init__("transport_error", message)


class UpstreamError(BridgeError):
    def __init__(self, stage: str, status: int):
        self.stage = stage
        self.status = status
        super().__init__("upstream_error", f"YouTube returned HTTP {status} at {stage}.")


class CaptionError(BridgeError):
    def __init__(self, code: str, message: str):
        super().__init__(code, message)
