# Workers Bridge MCP

![Workers Bridge MCP social preview](assets/workers-bridge-ogp-encyclopedia-dark.png)

Workers Bridge MCP is a stdio MCP server for authenticated YouTube, X, and
Reddit reads. It can relay through an Access-protected Cloudflare Worker, or
connect directly to destination hosts when no Worker is configured.

- `src/mcp_workers_bridge/`: the Python MCP server.
- `infra/service-bridge-workers/`: the Cloudflare Worker, tests, Terraform, and
  deployment runbook.

The server supports modern `2026-07-28` MCP requests and legacy `2025-11-25`
initialization-based clients. It does not contain Access secrets, AWS
credentials, cookies, transcripts, or cache data.

## Quick start

```bash
cd /Users/owner/mcp-workers-bridge
uv sync --system-certs --extra test
npm --prefix infra/service-bridge-workers ci
make check
```

Register the absolute executable with your MCP client:

```json
{
  "mcpServers": {
    "workers-bridge": {
      "command": "/Users/owner/mcp-workers-bridge/.venv/bin/mcp-workers-bridge",
      "args": []
    }
  }
}
```

Provide collector sessions through the client's protected environment:

```bash
export YOUTUBE_STORAGE_STATE_JSON='...'
export X_STORAGE_STATE_JSON='...'
export REDDIT_STORAGE_STATE_JSON='...'
```

To relay through the Worker, also set `SERVICE_BRIDGE_WORKER_URL`,
`CF_ACCESS_CLIENT_ID`, and `CF_ACCESS_CLIENT_SECRET`. With those unset, the
server uses direct mode.

Verify the server with `bridge_configuration`, then call the tool you need.

## Documentation

- [docs/manual.md](docs/manual.md): configuration, the 12 tools, cache,
  acceptance checks, client registration, and security boundaries.
- [docs/codex-cloud.md](docs/codex-cloud.md): Codex Cloud setup, secrets, OIDC,
  S3 permissions, publishing, and verification.
- [infra/service-bridge-workers/README.md](infra/service-bridge-workers/README.md):
  Worker deployment, Terraform, Access, and acceptance runbook.
- [infra/service-bridge-workers/API.md](infra/service-bridge-workers/API.md):
  HTTPS contract.
- [infra/service-bridge-workers/CLIENT-INTEGRATION.md](infra/service-bridge-workers/CLIENT-INTEGRATION.md):
  relay integration notes.
- [infra/service-bridge-workers/FEASIBILITY.md](infra/service-bridge-workers/FEASIBILITY.md):
  design evidence.

`make check` runs both test suites and the Worker build; Terraform 1.7+ is
only needed for `make terraform-check` and deployment.
