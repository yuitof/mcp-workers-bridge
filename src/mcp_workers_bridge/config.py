"""Environment-backed configuration with no credential logging."""

from dataclasses import dataclass
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

from .errors import ConfigurationError


TOKEN = re.compile(r"[!-~]+\Z")


def _positive_float(name: str, default: float) -> float:
    raw = os.environ.get(name, str(default))
    try:
        value = float(raw)
    except ValueError:
        raise ConfigurationError(f"{name} must be a number.") from None
    if not 1 <= value <= 600:
        raise ConfigurationError(f"{name} must be between 1 and 600 seconds.")
    return value


def _enabled(name: str) -> bool:
    return os.environ.get(name, "false").strip().lower() in {"1", "true", "yes", "on"}


def worker_origin(value: str) -> str:
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or not re.fullmatch(r"(?:[a-z0-9-]+\.)+[a-z][a-z0-9-]*", parsed.hostname)
            or parsed.port not in (None, 443)
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
            or re.search(r"[\x00-\x20\x7f\\]", value)
        ):
            raise ValueError
    except ValueError:
        raise ConfigurationError(
            "SERVICE_BRIDGE_WORKER_URL must be an HTTPS hostname on port 443."
        ) from None
    return "https://" + parsed.hostname


@dataclass(frozen=True)
class WorkersConfig:
    base_url: str
    client_id: str
    client_secret: str
    timeout: float

    @classmethod
    def from_env(cls) -> "WorkersConfig | None":
        base = (
            os.environ.get("SERVICE_BRIDGE_WORKER_URL")
            or os.environ.get("WORKERS_BRIDGE_URL")
            or ""
        ).strip()
        client_id = os.environ.get("CF_ACCESS_CLIENT_ID", "")
        client_secret = os.environ.get("CF_ACCESS_CLIENT_SECRET", "")
        if not base and not client_id and not client_secret:
            return None
        if not base:
            raise ConfigurationError("Set SERVICE_BRIDGE_WORKER_URL.")
        if not TOKEN.fullmatch(client_id):
            raise ConfigurationError(
                "Set CF_ACCESS_CLIENT_ID to the service-token value or Cloud HTTP-secret placeholder."
            )
        if not TOKEN.fullmatch(client_secret):
            raise ConfigurationError(
                "Set CF_ACCESS_CLIENT_SECRET to the service-token value or Cloud HTTP-secret placeholder."
            )
        return cls(
            base_url=worker_origin(base),
            client_id=client_id,
            client_secret=client_secret,
            timeout=_positive_float("WORKERS_BRIDGE_TIMEOUT_SECONDS", 50),
        )


@dataclass(frozen=True)
class LegacyConfig:
    enabled: bool
    vault_root: Path | None
    function: str
    table: str
    timeout: float

    @classmethod
    def from_env(cls) -> "LegacyConfig":
        root = os.environ.get("VAULT_ROOT", "").strip()
        return cls(
            enabled=_enabled("LEGACY_BRIDGE_ENABLED"),
            vault_root=Path(root).expanduser() if root else None,
            function=os.environ.get("SERVICE_BRIDGE_FUNCTION", "service-bridge"),
            table=os.environ.get("SERVICE_BRIDGE_TABLE", "service-bridge-sessions"),
            timeout=_positive_float("LEGACY_BRIDGE_TIMEOUT_SECONDS", 180),
        )
