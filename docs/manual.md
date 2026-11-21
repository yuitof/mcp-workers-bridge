# Workers Bridge MCP manual

Detailed usage, configuration, and operational behavior for the stdio MCP
server. For a quick start, see the [README](../README.md).

## Configuration

Use the MCP client's environment configuration or export these variables in
the process that starts the MCP server. Do not commit values.

```bash
export SERVICE_BRIDGE_WORKER_URL=https://service-bridge.example.workers.dev
export CF_ACCESS_CLIENT_ID=...
export CF_ACCESS_CLIENT_SECRET=...
export WORKERS_BRIDGE_TIMEOUT_SECONDS=50
export YOUTUBE_STORAGE_STATE_JSON='...'
export YOUTUBE_TIMEZONE=Europe/London
export LOCAL_CACHE_ROOT=/absolute/path/to/private-cache-root
export S3_CACHE_BUCKET=private-cache-bucket
export X_STORAGE_STATE_JSON='...'
export REDDIT_STORAGE_STATE_JSON='...'
```

`SERVICE_BRIDGE_WORKER_URL` and the Access pair are optional. When all three
are absent, the MCP runs in direct mode and connects to the destination hosts
directly with the supplied storage-state sessions. `bridge_configuration`
reports `mode = "direct"`, and `workers_bridge_probe` reports
`workers_not_configured`. The direct X, Reddit, and YouTube sessions set
`trust_env = False`, so `HTTP_PROXY`, `HTTPS_PROXY`, and `NO_PROXY` do not
apply to them.

### Modes

Set `MCP_WITHOUT_COOKIE = "1"` in the MCP server's `env` table to hide the
cookie-backed tools at registration. The remaining tools are
`bridge_configuration`, `workers_bridge_probe`,
`workers_youtube_captions`, `workers_x_asset`, `clear_cookie_state`, and
`clear_cache`.

Set `MCP_APPROVE_FOR_ME = "1"` to cache the Chrome Safe Storage password after
the first successful Keychain read. The cache is
`~/.private/mcp-workers-bridge/chrome-safe-storage`, mode 600. Remove it to
revoke the cached approval. `bridge_configuration` reports both mode flags.

The storage-state variables are optional. Cookie-backed tools require a
`profile` argument; if that profile is absent from the variable, the tool
extracts it from Chrome on macOS and caches the cookies for the MCP process.
Each variable may contain several profiles:

```json
{
  "profiles": {
    "Default": {"cookies": [...]},
    "Profile 1": {"cookies": [...]}
  }
}
```

`clear_cookie_state` removes selected profiles from the running MCP process.
Clearing is process-local; restarting Codex restores inherited values.

The preferred `LOCAL_CACHE_ROOT` layout drops `.codex/tmp`:

```text
$LOCAL_CACHE_ROOT/youtube/auth/storage-state.json
$LOCAL_CACHE_ROOT/x/auth/storage-state.json
$LOCAL_CACHE_ROOT/reddit/auth/storage-state.json
$LOCAL_CACHE_ROOT/youtube/history.json
$LOCAL_CACHE_ROOT/youtube/transcripts/<video-id>.<language>.json
```

Legacy `$LOCAL_CACHE_ROOT/.codex/tmp/<service>/...` paths are still read when
the preferred path does not exist.

In Codex Cloud, configure `CF_ACCESS_CLIENT_ID` and `CF_ACCESS_CLIENT_SECRET`
as environment-owned HTTP secrets scoped to the exact Worker hostname. Keep
the inherited HTTPS proxy active. On a local Mac, load the actual service-token
values privately and exclude the Worker hostname from the local proxy only when
your setup requires that route.

The URL configuration accepts `SERVICE_BRIDGE_WORKER_URL` and the shorter
`WORKERS_BRIDGE_URL` alias.

## MCP tools

| Tool | Network behavior |
| --- | --- |
| `bridge_configuration` | Reports configuration state without exposing credentials. |
| `workers_bridge_probe` | Checks `/v1/health` and the Gateway public-IP endpoint through the Worker. |
| `workers_youtube_captions` | Fetches one public video's English captions. Manual captions are preferred and automatic captions are the fallback. |
| `workers_youtube_history` | Retrieves authenticated account watch history, supports cache-only reads, and persists a vault-compatible local/S3 index. |
| `clear_cookie_state` | Clears selected cookie profiles from the running MCP process. |
| `clear_cache` | Deletes local cache artifacts, S3 cache objects, or both. |
| `workers_x_home` | Fetches authenticated `x.com/home`. |
| `workers_x_likes` | Discovers the current X read operation, verifies the Viewer identity, and collects authenticated Likes with cursor pagination. |
| `workers_x_bookmarks` | Discovers the current X read operation, verifies the Viewer identity, and collects authenticated Bookmarks with cursor pagination. |
| `workers_x_graphql` | Runs an allowlisted, read-only X GraphQL operation with caller-supplied authentication and transaction headers. |
| `workers_x_asset` | Fetches an allowlisted X web-client JavaScript asset. |
| `workers_reddit_identity` | Verifies the authenticated Reddit identity. |
| `workers_reddit_listing` | Fetches one JSON listing page for upvoted, saved, or another supported collection. |
| `workers_reddit_comments` | Fetches an authenticated Old Reddit post or comment page. |

`workers_youtube_captions` returns metadata and a segment count by default. Set
`include_segments=true` to return caption text. `max_segments` limits the
response to 20,000 segments.

`workers_youtube_history` defaults to `refresh=true`, `use_cache=true`, the
preceding 30 days, and 100 continuation pages. Set `refresh=false` for a
cache-only read or `use_cache=false` to disable caching. Pass `since` as
`YYYY-MM-DD`, override `timezone` with an IANA name such as `Europe/London`, or
set `max_pages`.

The tool returns normalized `video_id`, title, channel, URL, watch dates,
transcript status, coverage metadata, and cache status. Incomplete pagination
is reported as a partial result rather than an empty history.

`workers_x_likes` and `workers_x_bookmarks` read the session from
`X_STORAGE_STATE_JSON`, discover the current query IDs and transaction bundle
from X's authenticated web build, verify that the GraphQL Viewer matches the
home-page account, and return normalized post metadata and text. `max_pages`
accepts 1-200 and `count` accepts 1-100. A page limit, repeated cursor, or
malformed response is returned as an incomplete result with `next_cursor` when
available; it is never reported as an empty collection. Resume by passing that
value as `cursor`.

The cookie-backed tools (`workers_youtube_history`, `workers_x_home`,
`workers_x_graphql`, `workers_x_likes`, `workers_x_bookmarks`,
`workers_reddit_identity`, `workers_reddit_listing`, and
`workers_reddit_comments`) require a `profile` argument. It selects the
corresponding entry in the storage-state variable or extracts that profile
from Chrome.

Example cache-only read:

```json
{
  "refresh": false,
  "use_cache": true,
  "since": "2026-10-21"
}
```

YouTube, X, and Reddit sessions load from `YOUTUBE_STORAGE_STATE_JSON`,
`X_STORAGE_STATE_JSON`, and `REDDIT_STORAGE_STATE_JSON`. If a direct session
variable is absent, the server reads the corresponding standard private file
beneath `$LOCAL_CACHE_ROOT/<service>/auth/`. Cookies are placed in outbound request
headers by the MCP process and are never returned in tool metadata.

When the Worker is used, it must be deployed with `RELAY_ENABLED=true`. Its
relay exposes only fixed read-only operations. YouTube history uses the
authenticated feed only to obtain web-client context; watch-history cards are
read through the fixed `FEhistory` browse operation. X GraphQL query IDs are
dynamic, so `workers_x_graphql` accepts a query ID and supplies only the
documented read-operation names, exact query keys, `x.com`, and fixed endpoint
paths.

## YouTube cache

The MCP uses the same cache format and security boundaries as `vault`:

- `LOCAL_CACHE_ROOT` selects the private local cache root; the cache is stored
  under `$LOCAL_CACHE_ROOT/youtube`.
- `S3_CACHE_BUCKET` selects one S3 bucket for all cache data.
- The local index is `<cache>/history.json`; the S3 manifest is
  `youtube/history.json`.
- History checkpoints local progress after each page and conditionally updates
  the S3 manifest.
- S3 receives only validated transcript objects and the allowlisted history
  index. Sessions, cookies, browser profiles, scripts, and unrelated files are
  excluded.
- S3 uses ambient scoped AWS credentials in `us-west-1`, server-side AES-256
  encryption, conditional writes, and bounded conflict retries.
- A cache restore or upload failure is reported separately while local progress
  remains available.

`clear_cache` accepts `{"target": "local" | "s3" | "both", "service":
"youtube"}`. Local clearing deletes only `history.json` and `transcripts/`;
`auth/` and cookie state are never removed. S3 clearing deletes only objects
under the `youtube/` prefix.

## Live acceptance

The deployed Worker and MCP client passed read-only checks for:

- YouTube manual captions for `aircAruvnKk`
- Authenticated X home and `Viewer` GraphQL
- The main X web-client JavaScript bundle
- Reddit identity, upvoted and saved listings, and one comments page

Authenticated YouTube history and S3 synchronization have synthetic client and
Worker coverage but are not in that live-acceptance list. The Workers route
becomes callable only after the Worker revision containing `youtube/history`
and `youtube/browse` is deployed.

The live tests used private sessions from the local `vault` checkout. Tool
results omitted cookie values and source contents from logs.

## Register with an MCP client

After `uv sync`, use the absolute executable:

```json
{
  "mcpServers": {
    "workers-bridge": {
      "command": "/path/to/mcp-workers-bridge/.venv/bin/mcp-workers-bridge",
      "args": []
    }
  }
}
```

Pass configuration through the MCP client's protected environment or secret
injection. For a direct terminal test:

```bash
SERVICE_BRIDGE_WORKER_URL=https://... \
CF_ACCESS_CLIENT_ID=... \
CF_ACCESS_CLIENT_SECRET=... \
.venv/bin/mcp-workers-bridge
```

The process speaks MCP over standard input and output. Diagnostics belong on
standard error.

For Codex Cloud, see [codex-cloud.md](codex-cloud.md). Worker deployment and
acceptance are documented in the
[Worker runbook](../infra/service-bridge-workers/README.md).

## Security boundaries

- HTTPS and certificate verification remain enabled.
- Upstream origins are fixed. The MCP server accepts no arbitrary URL, CONNECT
  target, cookie, or authorization header.
- Cloudflare Access credentials are sent only to the Worker endpoint.
- Signed-out caption requests contain only the allowlisted caption headers and
  guest cookies returned by YouTube. History requests contain only scoped
  YouTube session cookies and the fixed authentication/context headers required
  by the vault implementation.
- Error objects contain fixed categories and upstream status codes. Raw
  credentials, cookies, response bodies, and signed caption URLs are not
  logged.
- S3 access is limited to the configured history manifest and validated
  transcript objects. Secrets and unrelated local files are never uploaded.
