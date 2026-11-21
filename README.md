# Workers Bridge MCP

[![PyPI](https://img.shields.io/pypi/v/mcp-workers-bridge.svg)](https://pypi.org/project/mcp-workers-bridge/)

![Workers Bridge MCP social preview](https://raw.githubusercontent.com/yuitof/mcp-workers-bridge/main/assets/workers-bridge-ogp-encyclopedia-dark.png)

Workers Bridge MCP is a stdio MCP server for authenticated YouTube, X, and
Reddit reads. It can relay through an Access-protected Cloudflare Worker, or
connect directly to destination hosts when no Worker is configured.

- `src/mcp_workers_bridge/`: the Python MCP server.
- `infra/service-bridge-workers/`: the Cloudflare Worker, tests, Terraform, and
  deployment runbook.

The server supports modern `2026-07-28` MCP requests and legacy `2025-11-25`
initialization-based clients. It does not contain Access secrets, AWS
credentials, cookies, transcripts, or cache data.

## Install

Workers Bridge MCP requires Python 3.12 or newer. `uv` provides the simplest
installation-free launcher:

```bash
uvx mcp-workers-bridge
```

Or install the command into a Python environment:

```bash
python -m pip install mcp-workers-bridge
mcp-workers-bridge
```

Running the command directly starts the stdio server and waits for an MCP
client. Register it with your client instead:

```json
{
  "mcpServers": {
    "workers-bridge": {
      "command": "uvx",
      "args": ["mcp-workers-bridge"]
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

## From source

```bash
git clone https://github.com/yuitof/mcp-workers-bridge.git
cd mcp-workers-bridge
uv sync --system-certs --extra test
npm --prefix infra/service-bridge-workers ci
make check
```

## Documentation

- [docs/manual.md](https://github.com/yuitof/mcp-workers-bridge/blob/main/docs/manual.md): configuration, the 12 tools, cache,
  acceptance checks, client registration, and security boundaries.
- [docs/codex-cloud.md](https://github.com/yuitof/mcp-workers-bridge/blob/main/docs/codex-cloud.md): Codex Cloud setup, secrets, OIDC,
  S3 permissions, publishing, and verification.
- [infra/service-bridge-workers/README.md](https://github.com/yuitof/mcp-workers-bridge/blob/main/infra/service-bridge-workers/README.md):
  Worker deployment, Terraform, Access, and acceptance runbook.
- [infra/service-bridge-workers/API.md](https://github.com/yuitof/mcp-workers-bridge/blob/main/infra/service-bridge-workers/API.md):
  HTTPS contract.
- [infra/service-bridge-workers/CLIENT-INTEGRATION.md](https://github.com/yuitof/mcp-workers-bridge/blob/main/infra/service-bridge-workers/CLIENT-INTEGRATION.md):
  relay integration notes.
- [infra/service-bridge-workers/FEASIBILITY.md](https://github.com/yuitof/mcp-workers-bridge/blob/main/infra/service-bridge-workers/FEASIBILITY.md):
  design evidence.
- [docs/releasing.md](https://github.com/yuitof/mcp-workers-bridge/blob/main/docs/releasing.md):
  PyPI Trusted Publishing setup and the release checklist.

`make check` runs both test suites and the Worker build; Terraform 1.7+ is
only needed for `make terraform-check` and deployment.

## License

Workers Bridge MCP is available under the [MIT License](https://github.com/yuitof/mcp-workers-bridge/blob/main/LICENSE).
