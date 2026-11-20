#!/usr/bin/env bash
set -euo pipefail

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

uv sync --system-certs --extra test
npm --prefix infra/service-bridge-workers ci

test -x .venv/bin/mcp-workers-bridge

codex_home="${CODEX_HOME:-$HOME/.codex}"
mkdir -p "$codex_home"

# Project-scoped MCP configuration is ignored unless the Cloud task trusts the
# checkout in its persistent user-level Codex configuration.
.venv/bin/python - "$codex_home/config.toml" "$root" <<'PY'
from pathlib import Path
import re
import sys

config = Path(sys.argv[1])
root = str(Path(sys.argv[2]).resolve())
header = f'[projects."{root}"]'
current = config.read_text() if config.exists() else ""
match = re.search(rf"(?m)^{re.escape(header)}\s*$", current)
if match:
    end = current.find("\n[", match.end())
    block_end = len(current) if end < 0 else end
    block = current[match.end():block_end]
    if re.search(r"(?m)^trust_level\s*=", block):
        block = re.sub(
            r'(?m)^trust_level\s*=\s*"[^"]*"\s*$',
            'trust_level = "trusted"',
            block,
            count=1,
        )
    else:
        block = '\ntrust_level = "trusted"' + block
    updated = current[:match.end()] + block + current[block_end:]
else:
    updated = (
        current.rstrip()
        + ("\n\n" if current.strip() else "")
        + header
        + '\ntrust_level = "trusted"\n'
    )
config.write_text(updated)
config.chmod(0o600)
PY
