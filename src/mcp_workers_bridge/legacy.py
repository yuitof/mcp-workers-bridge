"""Compatibility bridge to the documented Lambda client in an existing vault checkout."""

import os
from pathlib import Path
import re
import subprocess

from .config import LegacyConfig
from .errors import BridgeError


MAX_OUTPUT = 32_000


def _redact(value: str) -> str:
    value = re.sub(
        r"(?i)(authorization|proxy-authorization|cookie|set-cookie)\s*:\s*[^\r\n]+",
        r"\1: [redacted]",
        value,
    )
    return value[-MAX_OUTPUT:]


class LegacyBridge:
    def __init__(self, config: LegacyConfig):
        self.config = config

    def probe(self):
        if not self.config.enabled:
            raise BridgeError(
                "legacy_bridge_disabled",
                "Set LEGACY_BRIDGE_ENABLED=true to enable the Lambda bridge probe.",
            )
        if self.config.vault_root is None:
            raise BridgeError(
                "legacy_bridge_not_configured",
                "Set VAULT_ROOT to the existing vault checkout.",
            )
        script = self.config.vault_root / "tools/youtube/run.sh"
        if not script.is_file():
            raise BridgeError(
                "legacy_bridge_not_configured",
                "VAULT_ROOT does not contain tools/youtube/run.sh.",
            )
        env = os.environ.copy()
        env.setdefault("SERVICE_BRIDGE_FUNCTION", self.config.function)
        env.setdefault("SERVICE_BRIDGE_TABLE", self.config.table)
        try:
            result = subprocess.run(
                ["bash", str(script), "bridge", "--", "probe"],
                cwd=self.config.vault_root,
                env=env,
                capture_output=True,
                text=True,
                timeout=self.config.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise BridgeError(
                "legacy_bridge_timeout",
                "The Lambda bridge probe exceeded its configured timeout.",
            ) from None
        except OSError:
            raise BridgeError(
                "legacy_bridge_transport",
                "The Lambda bridge probe could not start.",
            ) from None
        return {
            "ok": result.returncode == 0,
            "exit_code": result.returncode,
            "stdout": _redact(result.stdout),
            "stderr": _redact(result.stderr),
        }
