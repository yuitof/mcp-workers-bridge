# Repository instructions

## Setup

Run the repository setup with elevated sandbox permissions before
MCP-dependent work. Setup writes outside the checkout (package-manager caches
and `$CODEX_HOME/config.toml`), so a sandbox limited to workspace writes fails.

```bash
bash /workspace/mcp-workers-bridge/.codex/setup.sh
```

Use that absolute path in Codex Cloud. Locally, run
`bash "$(git rev-parse --show-toplevel)/.codex/setup.sh"` instead.

Skip the run when `.venv/bin/mcp-workers-bridge` exists and
`$CODEX_HOME/config.toml` already contains the repository's current trust
entry and MCP server definition.
