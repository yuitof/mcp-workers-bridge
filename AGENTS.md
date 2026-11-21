# Repository instructions

## Setup

Run setup before MCP-dependent work using `exec_command` with elevated
permissions:

```text
cmd: bash /workspace/mcp-workers-bridge/.codex/setup.sh
sandbox_permissions: require_escalated
justification: Setup writes package caches and $CODEX_HOME/config.toml outside
the workspace.
```

Use the absolute path above in Codex Cloud. Locally, use
`bash "$(git rev-parse --show-toplevel)/.codex/setup.sh"` as the command with
the same `sandbox_permissions: require_escalated` setting.

If a setup run fails, run it again with `sandbox_permissions:
require_escalated` before treating the failure as a blocker.

Skip the run when `.venv/bin/mcp-workers-bridge` exists and
`$CODEX_HOME/config.toml` already contains the repository's current trust
entry and MCP server definition.
