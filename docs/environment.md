# Environment variables

Reference for variables read by the MCP server. For a Codex CLI setup example,
see the [README](../README.md).

## How variables reach the MCP

Codex starts the MCP server as a child process. Values can arrive in two ways:

- `env_vars = [...]` in the server table lists variable names to forward from
  the Codex process environment.
- `env = { KEY = "value" }` sets literal values in the child environment.

`env_vars` never assigns values. Export variables in the shell or app
environment that launches Codex, then restart Codex after changes. TOML does
not expand `$HOME` or `$VAR`, so literal paths in `env` must be absolute.

## Workers bridge

| Variable | Default | Purpose |
| --- | --- | --- |
| `SERVICE_BRIDGE_WORKER_URL` | unset | HTTPS Worker endpoint. Required for Worker-backed tools when the bridge is used. |
| `WORKERS_BRIDGE_URL` | unset | Alias for `SERVICE_BRIDGE_WORKER_URL`. |
| `CF_ACCESS_CLIENT_ID` | unset | Cloudflare Access service-token client ID. |
| `CF_ACCESS_CLIENT_SECRET` | unset | Cloudflare Access service-token secret. |
| `WORKERS_BRIDGE_TIMEOUT_SECONDS` | `50` | Per-request timeout, 1-600 seconds. |

If the URL and both Access values are absent, Worker-backed tools report that
the bridge is not configured. If the URL is present, both Access values are
required. Direct mode uses destination connections when the Worker is absent.

## Collector sessions

| Variable | Default | Purpose |
| --- | --- | --- |
| `YOUTUBE_STORAGE_STATE_JSON` | unset | YouTube cookie storage state. |
| `X_STORAGE_STATE_JSON` | unset | X cookie storage state. |
| `REDDIT_STORAGE_STATE_JSON` | unset | Reddit cookie storage state. |
| `YOUTUBE_TIMEZONE` | `Europe/London` | IANA timezone for history date boundaries. |

Cookie-backed tools require a `profile` tool argument such as `Default` or
`Profile 1`. A storage-state variable may hold one profile:

```json
{"cookies": [...]}
```

or several:

```json
{
  "profiles": {
    "Default": {"cookies": [...]},
    "Profile 1": {"cookies": [...]}
  }
}
```

The old single-profile form is treated as `Default`. If the requested profile
is absent, the server extracts it from Chrome on macOS.

## Chrome storage-state exporter

The package includes the `chrome-storage-state` console tool. It reads Chrome
cookies on macOS and prints storage-state JSON with `--stdout`:

```bash
uvx --system-certs --from mcp-workers-bridge chrome-storage-state youtube \
  --profile "Default" --stdout
```

Capture the output directly into the matching variable:

```bash
export YOUTUBE_STORAGE_STATE_JSON="$(
  uvx --system-certs --from mcp-workers-bridge chrome-storage-state youtube \
    --profile "Default" --stdout
)"
export X_STORAGE_STATE_JSON="$(
  uvx --system-certs --from mcp-workers-bridge chrome-storage-state x \
    --profile "Default" --stdout
)"
export REDDIT_STORAGE_STATE_JSON="$(
  uvx --system-certs --from mcp-workers-bridge chrome-storage-state reddit \
    --profile "Default" --stdout
)"
```

`SERVICE` accepts `youtube`, `x`, or `reddit`. `--profile` accepts `Default` or
`Profile <number>`. Use `--output PATH` instead of `--stdout` to write a
mode-600 file. The tool exits with an error if the profile is not signed in,
the required cookie names are missing, or extraction is disabled in Codex
Cloud.

For a clipboard-based macOS flow, be aware that other applications can read
the clipboard:

```bash
uvx --system-certs --from mcp-workers-bridge chrome-storage-state youtube \
  --profile "Default" --stdout | pbcopy
export YOUTUBE_STORAGE_STATE_JSON="$(pbpaste)"
pbcopy </dev/null
```

## Cookie modes

| Variable | Default | Purpose |
| --- | --- | --- |
| `MCP_WITHOUT_COOKIE` | false | Hide cookie-backed tools at server startup. |
| `MCP_APPROVE_FOR_ME` | false | Cache the Chrome Safe Storage password after the first approval. |
| `MCP_DISABLE_COOKIE_EXTRACTION` | false | Refuse automatic Chrome extraction explicitly. |
| `MCP_CLOUD_MODE` | false | Treat the process as Codex Cloud. |
| `CODEX_CLOUD` | environment-owned | Codex Cloud indicator. |

Truthy values are `1`, `true`, `yes`, and `on` (case-insensitive). In
`MCP_WITHOUT_COOKIE` mode, only `bridge_configuration`,
`workers_bridge_probe`, `workers_youtube_captions`, `workers_x_asset`,
`clear_cookie_state`, and `clear_cache` are registered.

Automatic extraction is always disabled in Codex Cloud. Detection uses
`CODEX_CLOUD`, `MCP_CLOUD_MODE`, or Linux with `HOME=/home/agent`.
`MCP_APPROVE_FOR_ME` stores the Keychain password at
`~/.private/mcp-workers-bridge/chrome-safe-storage` with mode 600; delete that
file to revoke the cached approval.

## Local and S3 cache

| Variable | Default | Purpose |
| --- | --- | --- |
| `LOCAL_CACHE_ROOT` | unset | Private root for local sessions and YouTube cache. |
| `S3_CACHE_BUCKET` | unset | Single bucket for S3 cache mirroring. |

The cache lives at `$LOCAL_CACHE_ROOT/youtube`:

```text
history.json
transcripts/<video-id>.<language>.json
auth/storage-state.json
```

The directory is created on first use and must be writable. Without
`LOCAL_CACHE_ROOT`, history refresh works without persistence. S3 uses the
`youtube/` prefix in `S3_CACHE_BUCKET`. `clear_cache` accepts
`{"target": "local"|"s3"|"both"}`; local clearing removes only `history.json`
and `transcripts/`, never `auth/`.

## AWS

These use boto3's normal credential chain for `S3_CACHE_BUCKET`:

| Variable | Default | Purpose |
| --- | --- | --- |
| `AWS_REGION` | boto3 default | Ambient AWS region for boto3 clients; the S3 cache client uses `us-west-1` explicitly. |
| `AWS_PROFILE` | unset | Named AWS profile. |
| `AWS_CONFIG_FILE` | `~/.aws/config` | AWS profile configuration file. |
| `AWS_SHARED_CREDENTIALS_FILE` | `~/.aws/credentials` | AWS shared credentials file. |
| `AWS_WEB_IDENTITY_TOKEN_FILE` | unset | Web-identity token file. |
| `AWS_ROLE_ARN` | unset | Web-identity role ARN. |
| `AWS_CA_BUNDLE` | unset | Custom CA bundle for boto3. |

When `AWS_PROFILE` is provided, forward `AWS_CONFIG_FILE` and
`AWS_SHARED_CREDENTIALS_FILE` too if they are non-default.

## TLS and proxy

| Variable | Default | Purpose |
| --- | --- | --- |
| `SSL_CERT_FILE` | unset | CA bundle used explicitly by the Worker HTTP client. |
| `REQUESTS_CA_BUNDLE` | unset | CA bundle used by requests. |
| `CURL_CA_BUNDLE` | unset | CA bundle fallback used by requests/libcurl tooling. |
| `HTTP_PROXY` | unset | Proxy for HTTP requests. |
| `HTTPS_PROXY` | unset | Proxy for HTTPS requests. |
| `NO_PROXY` | unset | Hosts/CIDRs that bypass the proxy. |

Cloud environments commonly require the inherited HTTPS proxy and trusted CA.
Cookie-backed direct sessions set `trust_env = False`, so proxy variables do
not apply to those destination requests.

## Setup only

These are read by `.codex/setup.sh`, not by the MCP server:

| Variable | Default | Purpose |
| --- | --- | --- |
| `MCP_WORKERS_BRIDGE_SETUP_CACHE` | `/tmp` temp dir | Writable root for uv/npm/Wrangler setup caches. |
| `CODEX_HOME` | `~/.codex` | Destination for the injected Codex config. |

## Removed or renamed

- `YOUTUBE_CACHE_DIR` was removed; use `LOCAL_CACHE_ROOT`.
- `YOUTUBE_CACHE_BUCKET` was removed; use `S3_CACHE_BUCKET`.
- `VAULT_ROOT` was removed; use `LOCAL_CACHE_ROOT`.
- `MCP_CHROME_PROFILE` and `MCP_CHROME_ROOT` are not part of the supported
  interface; use the tool's `profile` argument and the standard macOS Chrome
  location.
