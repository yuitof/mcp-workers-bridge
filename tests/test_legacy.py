from pathlib import Path
import subprocess

import pytest

from mcp_workers_bridge.config import LegacyConfig
from mcp_workers_bridge.errors import BridgeError
from mcp_workers_bridge.legacy import LegacyBridge


def test_legacy_probe_is_explicitly_disabled(tmp_path):
    bridge = LegacyBridge(
        LegacyConfig(
            enabled=False,
            vault_root=tmp_path,
            function="service-bridge",
            table="service-bridge-sessions",
            timeout=30,
        )
    )
    with pytest.raises(BridgeError, match="LEGACY_BRIDGE_ENABLED=true"):
        bridge.probe()


def test_legacy_probe_runs_only_documented_command(monkeypatch, tmp_path):
    script = tmp_path / "tools/youtube/run.sh"
    script.parent.mkdir(parents=True)
    script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    captured = {}

    def run(command, **kwargs):
        captured["command"] = command
        captured["cwd"] = kwargs["cwd"]
        return subprocess.CompletedProcess(command, 0, '{"bridge":"verified"}\n', "")

    monkeypatch.setattr(subprocess, "run", run)
    bridge = LegacyBridge(
        LegacyConfig(
            enabled=True,
            vault_root=Path(tmp_path),
            function="service-bridge",
            table="service-bridge-sessions",
            timeout=30,
        )
    )
    result = bridge.probe()
    assert result["ok"] is True
    assert captured["command"] == [
        "bash",
        str(script),
        "bridge",
        "--",
        "probe",
    ]
    assert captured["cwd"] == Path(tmp_path)
