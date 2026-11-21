# Workers Bridge MCP

![Workers Bridge MCP social preview](assets/workers-bridge-ogp-encyclopedia-dark.png)

This repository contains both halves of the Access-protected service bridge:

- `src/mcp_workers_bridge/`: the Python stdio MCP client.
- `infra/service-bridge-workers/`: the Cloudflare Worker source, tests, build configuration, Terraform, and deployment runbook.

The stdio server is dual-era: the same executable supports modern
`2026-07-28` requests and legacy `2025-11-25` initialization-based clients.

The server does not contain Cloudflare Access secrets, AWS credentials, cookies, transcripts, or cache data.

## Repository setup

```bash
cd /Users/owner/mcp-workers-bridge
uv sync --system-certs --extra test
npm --prefix infra/service-bridge-workers ci
```

Run both test suites and the Worker build:

```bash
make check
```

Or run them separately with `make python-test`, `make worker-check`, `make worker-test`, `make worker-build`, and `make terraform-check`.

The repository also includes a project-scoped Codex MCP configuration and Cloud
setup script. See [docs/codex-cloud.md](docs/codex-cloud.md) for environment
variables, secret scopes, OIDC/S3 permissions, publishing, and verification.

## Configuration

Use the MCP client's environment configuration or export these variables in the process that starts the MCP server. Do not commit values.

### Workers bridge

```bash
export SERVICE_BRIDGE_WORKER_URL=https://service-bridge-workers.example.workers.dev
export CF_ACCESS_CLIENT_ID=...
export CF_ACCESS_CLIENT_SECRET=...
export WORKERS_BRIDGE_TIMEOUT_SECONDS=50
export YOUTUBE_STORAGE_STATE_JSON='...'
export YOUTUBE_TIMEZONE=Europe/London
export YOUTUBE_CACHE_DIR=/absolute/path/to/private-cache
export YOUTUBE_CACHE_BUCKET=private-youtube-cache-bucket
export X_STORAGE_STATE_JSON='...'
export REDDIT_STORAGE_STATE_JSON='...'
```

In Codex Cloud, configure `CF_ACCESS_CLIENT_ID` and `CF_ACCESS_CLIENT_SECRET` as environment-owned HTTP secrets scoped to the exact Worker hostname. Keep the inherited HTTPS proxy active. On a local Mac, load the actual service-token values privately and exclude the Worker hostname from the local proxy only when your setup requires that route.

The URL configuration accepts `SERVICE_BRIDGE_WORKER_URL` and the shorter `WORKERS_BRIDGE_URL` alias.

## Worker service

The Worker subproject includes fixed-target Gateway routing, signed-out caption diagnostics and relay, authenticated YouTube history operations, and allowlisted X/Reddit reads. Its local, acceptance, Terraform, and deployment instructions are in [infra/service-bridge-workers/README.md](infra/service-bridge-workers/README.md), with the HTTPS contract in [API.md](infra/service-bridge-workers/API.md).

```bash
npm --prefix infra/service-bridge-workers run check
npm --prefix infra/service-bridge-workers test -- --run
npm --prefix infra/service-bridge-workers run build
terraform -chdir=infra/service-bridge-workers/terraform fmt -check
terraform -chdir=infra/service-bridge-workers/terraform init -backend=false
terraform -chdir=infra/service-bridge-workers/terraform validate
```

Deployment, Worker creation, Access changes, Terraform apply, and Gateway changes still require the approvals documented in the Worker runbook. The checked-in Wrangler configuration keeps diagnostics and relay routes disabled.

### Deploy the Worker

Use Node 22.12+, 24, or 26+ and Terraform 1.7+. Authenticate Wrangler with a token that can write Workers:

```bash
cd /Users/owner/mcp-workers-bridge/infra/service-bridge-workers
export CLOUDFLARE_ACCOUNT_ID="<account ID>"
export CLOUDFLARE_API_TOKEN="<deployment token>"

npm ci
npm run check
npm test
npm run build
npx wrangler whoami
```

For a new Worker, deploy the checked-in safe configuration first. It creates no public URL and keeps every route disabled:

```bash
npx wrangler deploy
```

Record the deployment ID privately. Use a read-only Cloudflare token with Worker and Access read permissions to obtain the stable Worker ID:

```bash
export CLOUDFLARE_API_TOKEN="<read-only inspection token>"
npm run inspect
```

The Worker must exist before Terraform creates its Access application. Keep variables, plans, and state outside the repository:

```bash
export BRIDGE_STATE_DIR="$HOME/.private/service-bridge-workers"
mkdir -p "$BRIDGE_STATE_DIR"
chmod 700 "$BRIDGE_STATE_DIR"

cp terraform/account.tfvars.example "$BRIDGE_STATE_DIR/account.tfvars"
chmod 600 "$BRIDGE_STATE_DIR/account.tfvars"
```

Set the real `account_id` and `worker_id` in that private variables file. Review the complete Terraform plan before applying:

```bash
terraform -chdir=terraform init -backend=false

terraform -chdir=terraform plan \
  -state="$BRIDGE_STATE_DIR/terraform.tfstate" \
  -var-file="$BRIDGE_STATE_DIR/account.tfvars" \
  -out="$BRIDGE_STATE_DIR/access.tfplan"

terraform -chdir=terraform show \
  -no-color "$BRIDGE_STATE_DIR/access.tfplan"
```

After approval, apply that reviewed plan:

```bash
terraform -chdir=terraform apply \
  -input=false \
  -state="$BRIDGE_STATE_DIR/terraform.tfstate" \
  "$BRIDGE_STATE_DIR/access.tfplan"
```

Store the sensitive Terraform outputs directly in a private secret manager or environment configuration. Do not print or commit them:

```text
service_token_client_id
service_token_client_secret
```

Create the ignored private deployment profile from `wrangler.jsonc`:

```bash
cp wrangler.jsonc wrangler.acceptance.local.jsonc
```

First deploy an Access-test profile. In the copied configuration, change these fields while leaving diagnostics and relays disabled:

```json
{
  "workers_dev": true,
  "preview_urls": true,
  "vars": {
    "DIAGNOSTICS_ENABLED": "false",
    "COMPARISON_ENABLED": "false",
    "RELAY_ENABLED": "false"
  }
}
```

Deploy it and test the production and version-preview URLs:

```bash
npx wrangler deploy --config wrangler.acceptance.local.jsonc

export SERVICE_BRIDGE_WORKER_URL="https://service-bridge-workers.<subdomain>.workers.dev"
export SERVICE_BRIDGE_WORKER_PREVIEW_URL="<exact version preview URL>"
export CF_ACCESS_CLIENT_ID="<from Terraform output>"
export CF_ACCESS_CLIENT_SECRET="<from Terraform output>"

npm run acceptance -- access
```

Missing and invalid credentials must be denied; valid credentials must reach the Worker. Then set `vars.DIAGNOSTICS_ENABLED` to `"true"` in the private configuration, redeploy, and verify Gateway egress and captions:

```bash
npx wrangler deploy --config wrangler.acceptance.local.jsonc
npm run acceptance -- egress
npm run acceptance -- captions
```

Finally restore `"DIAGNOSTICS_ENABLED": "false"`, set `"preview_urls": false`, and enable the operational routes:

```json
{
  "workers_dev": true,
  "preview_urls": false,
  "vars": {
    "DIAGNOSTICS_ENABLED": "false",
    "COMPARISON_ENABLED": "true",
    "RELAY_ENABLED": "true"
  }
}
```

```bash
npx wrangler deploy --config wrangler.acceptance.local.jsonc
```

To verify Access from Codex Cloud, run the acceptance script in the Cloud outer executor with `NODE_USE_ENV_PROXY=1` and the existing trusted proxy/CA configuration. An exact-hostname CONNECT denial occurs before the Worker or origin and is not an MCP authentication failure.

## MCP tools

| Tool | Network behavior |
| --- | --- |
| `bridge_configuration` | Reports configuration state without exposing credentials. |
| `workers_bridge_probe` | Checks `/v1/health` and the Gateway public-IP endpoint through the Worker. |
| `workers_youtube_captions` | Fetches one public video's English captions through the Worker. Manual captions are preferred and automatic captions are the fallback. |
| `workers_youtube_history` | Retrieves authenticated account watch history through Workers, supports cache-only reads, and persists a vault-compatible local/S3 index. |
| `workers_x_home` | Fetches authenticated `x.com/home`. |
| `workers_x_likes` | Discovers the current X read operation, verifies the Viewer identity, and collects authenticated Likes with cursor pagination. |
| `workers_x_bookmarks` | Discovers the current X read operation, verifies the Viewer identity, and collects authenticated Bookmarks with cursor pagination. |
| `workers_x_graphql` | Runs an allowlisted, read-only X GraphQL operation with caller-supplied authentication and transaction headers. |
| `workers_x_asset` | Fetches an allowlisted X web-client JavaScript asset. |
| `workers_reddit_identity` | Verifies the authenticated Reddit identity. |
| `workers_reddit_listing` | Fetches one JSON listing page for upvoted, saved, or another supported collection. |
| `workers_reddit_comments` | Fetches an authenticated Old Reddit post or comment page. |

`workers_youtube_captions` returns metadata and a segment count by default. Set `include_segments=true` to return caption text. `max_segments` limits the response to 20,000 segments.

`workers_youtube_history` defaults to `refresh=true`, `use_cache=true`, the preceding 30 days, and 100 continuation pages. Set `refresh=false` for a cache-only read or `use_cache=false` to disable caching. Pass `since` as `YYYY-MM-DD`, override `timezone` with an IANA name such as `Europe/London`, or set `max_pages`.

The tool returns normalized `video_id`, title, channel, URL, watch dates, transcript status, coverage metadata, and cache status. Incomplete pagination is reported as a partial result rather than an empty history.

`workers_x_likes` and `workers_x_bookmarks` read the session from `X_STORAGE_STATE_JSON`, discover the current query IDs and transaction bundle from X's authenticated web build, verify that the GraphQL Viewer matches the home-page account, and return normalized post metadata and text. `max_pages` accepts 1–200 and `count` accepts 1–100. A page limit, repeated cursor, or malformed response is returned as an incomplete result with `next_cursor` when available; it is never reported as an empty collection. Resume by passing that value as `cursor`.

Example cache-only Workers read:

```json
{
  "refresh": false,
  "use_cache": true,
  "since": "2026-10-21"
}
```

YouTube, X, and Reddit sessions load from `YOUTUBE_STORAGE_STATE_JSON`, `X_STORAGE_STATE_JSON`, and `REDDIT_STORAGE_STATE_JSON`. If a direct session variable is absent, the server reads the corresponding standard private file beneath `VAULT_ROOT/.codex/tmp/`. Cookies are placed in outbound request headers by the MCP process and are never returned in tool metadata.

The Worker must be deployed with `RELAY_ENABLED=true`. Its relay exposes only fixed read-only operations. YouTube history uses the authenticated feed only to obtain web-client context; watch-history cards are read through the fixed `FEhistory` browse operation. X GraphQL query IDs are dynamic, so `workers_x_graphql` accepts a query ID and supplies only the documented read-operation names, exact query keys, `x.com`, and fixed endpoint paths.

### YouTube cache

The MCP uses the same cache format and security boundaries as `vault`:

- `YOUTUBE_CACHE_DIR` selects the private local cache. If unset, `VAULT_ROOT/.codex/tmp/youtube` is used.
- `YOUTUBE_CACHE_BUCKET` selects S3. If unset, `<cache>/s3-config.json` may provide `{"bucket":"..."}`.
- The local index is `<cache>/history.json`; the S3 manifest is `cache/v1/history.json`.
- Workers history checkpoints local progress after each page and conditionally updates the S3 manifest.
- S3 receives only validated transcript objects and the allowlisted history index. Sessions, cookies, browser profiles, scripts, and unrelated files are excluded.
- S3 uses ambient scoped AWS credentials in `us-west-1`, server-side AES-256 encryption, conditional writes, and bounded conflict retries.
- A cache restore or upload failure is reported separately while local progress remains available. Cache failure does not cause a direct-network fallback.

## Live acceptance

The deployed Worker and MCP client passed read-only checks for:

- YouTube manual captions for `aircAruvnKk`
- Authenticated X home and `Viewer` GraphQL
- The main X web-client JavaScript bundle
- Reddit identity, upvoted and saved listings, and one comments page

Authenticated YouTube history and S3 synchronization have synthetic client and Worker coverage but are not in that live-acceptance list. The Workers route becomes callable only after the Worker revision containing `youtube/history` and `youtube/browse` is deployed.

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
- Signed-out caption requests contain only the allowlisted caption headers and guest cookies returned by YouTube. History requests contain only scoped YouTube session cookies and the fixed authentication/context headers required by the vault implementation.
- Error objects contain fixed categories and upstream status codes. Raw credentials, cookies, response bodies, and signed caption URLs are not logged.
- S3 access is limited to the configured history manifest and validated transcript objects. Secrets and unrelated local files are never uploaded.
