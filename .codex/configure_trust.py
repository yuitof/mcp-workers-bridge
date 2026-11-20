"""Register a trusted Codex checkout without requiring a writable config inode."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import tomllib


def updated_config(current: str, root: str) -> str:
    header = f"[projects.{json.dumps(root, ensure_ascii=False)}]"
    match = re.search(rf"(?m)^{re.escape(header)}\s*$", current)
    if match:
        end = current.find("\n[", match.end())
        block_end = len(current) if end < 0 else end
        block = current[match.end() : block_end]
        if re.search(r"(?m)^trust_level\s*=", block):
            block = re.sub(
                r'(?m)^trust_level\s*=\s*"[^"]*"\s*$',
                'trust_level = "trusted"',
                block,
                count=1,
            )
        else:
            block = '\ntrust_level = "trusted"' + block
        return current[: match.end()] + block + current[block_end:]
    return (
        current.rstrip()
        + ("\n\n" if current.strip() else "")
        + header
        + '\ntrust_level = "trusted"\n'
    )


def write_updated_config(config: Path, content: str) -> None:
    """Replace config atomically; the existing file may be mounted read-only."""
    target = config.resolve() if config.is_symlink() else config
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_error: OSError | None = None
    fd = -1
    temporary: Path | None = None
    try:
        fd, name = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
        )
        temporary = Path(name)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = -1
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o600)
        os.replace(temporary, target)
        return
    except OSError as error:
        atomic_error = error
    finally:
        if fd >= 0:
            os.close(fd)
        if temporary is not None:
            temporary.unlink(missing_ok=True)

    try:
        mode = stat.S_IMODE(target.stat().st_mode)
        target.chmod(mode | stat.S_IWUSR)
        target.write_text(content, encoding="utf-8")
        target.chmod(0o600)
        return
    except OSError as direct_error:
        raise SystemExit(
            "Could not register Codex project trust: both atomic replacement "
            f"and direct write failed for {config} (atomic: {atomic_error}; "
            f"direct: {direct_error}). The Codex home directory must permit "
            "creating or replacing config.toml. Provision the trust entry in "
            "the Cloud environment's persistent CODEX_HOME instead."
        ) from None


def is_trusted(current: str, project: str) -> bool:
    try:
        settings = tomllib.loads(current)
    except tomllib.TOMLDecodeError:
        return False
    projects = settings.get("projects", {})
    entry = projects.get(project, {}) if isinstance(projects, dict) else {}
    return isinstance(entry, dict) and entry.get("trust_level") == "trusted"


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: configure_trust.py CONFIG PROJECT_ROOT")
    config = Path(sys.argv[1])
    root = str(Path(sys.argv[2]).resolve())
    current = config.read_text(encoding="utf-8") if config.exists() else ""
    if is_trusted(current, root):
        try:
            config.chmod(0o600)
        except OSError:
            pass
        return
    updated = updated_config(current, root)
    write_updated_config(config, updated)


if __name__ == "__main__":
    main()
