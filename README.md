# Workers Bridge MCP

A standalone stdio MCP server for the service bridge implementations:

- `service-bridge-workers`: direct Access-protected HTTPS client with YouTube captions, X read operations, and authenticated Reddit listing/source operations.
- `service-bridge`: optional compatibility probe that invokes the documented client in an existing `vault` checkout, preserving its AWS, built-in VPN, proxy, CA, and cache settings.

The server does not contain Cloudflare Access secrets, AWS credentials, cookies, transcripts, or cache data.

## Install

```bash
cd /Users/owner/mcp-workers-bridge
uv sync --system-certs --extra test
.venv/bin/pytest -q
```

## Configuration

Use the MCP client's environment configuration or export these variables in the process that starts the MCP server. Do not commit values.

### Workers bridge

```bash
export SERVICE_BRIDGE_WORKER_URL=https://service-bridge-workers.example.workers.dev
export CF_ACCESS_CLIENT_ID=...
export CF_ACCESS_CLIENT_SECRET=...
export WORKERS_BRIDGE_TIMEOUT_SECONDS=50
export X_STORAGE_STATE_JSON='...'
export REDDIT_STORAGE_STATE_JSON='...'
```

In Codex Cloud, configure `CF_ACCESS_CLIENT_ID` and `CF_ACCESS_CLIENT_SECRET` as environment-owned HTTP secrets scoped to the exact Worker hostname. Keep the inherited HTTPS proxy active. On a local Mac, load the actual service-token values privately and exclude the Worker hostname from the local proxy only when your setup requires that route.

The URL configuration accepts `SERVICE_BRIDGE_WORKER_URL` and the shorter `WORKERS_BRIDGE_URL` alias.

### Legacy Lambda bridge

```bash
export LEGACY_BRIDGE_ENABLED=true
export VAULT_ROOT=/absolute/path/to/english-vault
export SERVICE_BRIDGE_FUNCTION=service-bridge
export SERVICE_BRIDGE_TABLE=service-bridge-sessions
export LEGACY_BRIDGE_TIMEOUT_SECONDS=180
```

The legacy tool runs only:

```text
bash tools/youtube/run.sh bridge -- probe
```

The child process inherits the existing AWS identity, built-in VPN, proxy, CA, S3, and cache configuration. It never runs a collector or reads cookies. The legacy tool is opt-in because its connectivity depends on the private `service-bridge` deployment.

## MCP tools

| Tool | Network behavior |
| --- | --- |
| `bridge_configuration` | Reports configuration state without exposing credentials. |
| `workers_bridge_probe` | Checks `/v1/health` and the Gateway public-IP endpoint through the Worker. |
| `workers_youtube_captions` | Fetches one public video's English captions through the Worker. Manual captions are preferred and automatic captions are the fallback. |
| `workers_x_home` | Fetches authenticated `x.com/home`. |
| `workers_x_graphql` | Runs an allowlisted, read-only X GraphQL operation with caller-supplied authentication and transaction headers. |
| `workers_x_asset` | Fetches an allowlisted X web-client JavaScript asset. |
| `workers_reddit_identity` | Verifies the authenticated Reddit identity. |
| `workers_reddit_listing` | Fetches one JSON listing page for upvoted, saved, or another supported collection. |
| `workers_reddit_comments` | Fetches an authenticated Old Reddit post or comment page. |
| `legacy_bridge_probe` | Runs the unsigned probe of the existing Lambda bridge through `VAULT_ROOT`. |

`workers_youtube_captions` returns metadata and a segment count by default. Set `include_segments=true` to return caption text. `max_segments` limits the response to 20,000 segments.

X and Reddit sessions load from `X_STORAGE_STATE_JSON` or `REDDIT_STORAGE_STATE_JSON`. If those direct variables are absent, the server reads their standard private files beneath `VAULT_ROOT/.codex/tmp/`. Cookies are placed in outbound request headers by the MCP process and are never returned in tool metadata.

The Worker must be deployed with `RELAY_ENABLED=true`. Its relay exposes only fixed read-only operations. X GraphQL query IDs are dynamic, so `workers_x_graphql` accepts a query ID and supplies only the documented read-operation names, exact query keys, `x.com`, and fixed endpoint paths.

## Live acceptance

The deployed Worker and MCP client passed read-only checks for:

- YouTube manual captions for `aircAruvnKk`
- Authenticated X home and `Viewer` GraphQL
- The main X web-client JavaScript bundle
- Reddit identity, upvoted and saved listings, and one comments page

The live tests used private sessions from the local `vault` checkout. Tool results omitted cookie values and source contents from logs.

## Register with an MCP client

After `uv sync`, use the absolute executable:

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

Pass configuration through the MCP client's protected environment or secret injection. For a direct terminal test:

```bash
SERVICE_BRIDGE_WORKER_URL=https://... \
CF_ACCESS_CLIENT_ID=... \
CF_ACCESS_CLIENT_SECRET=... \
.venv/bin/mcp-workers-bridge
```

The process speaks MCP over standard input and output. Diagnostics belong on standard error.

## Security boundaries

- HTTPS and certificate verification remain enabled.
- Upstream origins are fixed by the Worker API. The MCP server accepts no arbitrary URL, CONNECT target, cookie, or authorization header.
- Cloudflare Access credentials are sent only to the Worker endpoint.
- YouTube requests are signed out and contain only the allowlisted caption headers and guest cookies returned by YouTube.
- Error objects contain fixed categories and upstream status codes. Raw credentials, cookies, response bodies, and signed caption URLs are not logged.
- The server performs no S3 reads or writes. The optional legacy probe delegates to the existing client without collecting data.
