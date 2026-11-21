# Workers Bridge MCP

![Workers Bridge MCP social preview](assets/workers-bridge-ogp-encyclopedia-dark.png)

This guide connects the stdio MCP server to Codex CLI and fetches authenticated
YouTube, X, and Reddit data through direct destination connections. Worker
relay configuration is covered in [docs/manual.md](docs/manual.md).

## Before you start

- macOS for the bundled Chrome cookie exporter (manual DevTools export works on
  any platform).
- Python 3.12+ and [uv](https://docs.astral.sh/uv/).
- Codex CLI.
- Chrome signed in to the services you want to read, in the profile you will
  export from.

## 1. Extract cookies from Chrome

Cookies are full account credentials. Keep the values private, never commit
them, and never paste them into chat.

### Option A: bundled exporter (macOS)

Extract each service directly into its environment variable with uvx. This
requires `mcp-workers-bridge` 0.6.0 or newer:

```bash
export YOUTUBE_STORAGE_STATE_JSON="$(
  uvx --system-certs --from 'mcp-workers-bridge[cookies]' \
    chrome-storage-state youtube --profile "Default" --stdout
)"
export X_STORAGE_STATE_JSON="$(
  uvx --system-certs --from 'mcp-workers-bridge[cookies]' \
    chrome-storage-state x --profile "Default" --stdout
)"
export REDDIT_STORAGE_STATE_JSON="$(
  uvx --system-certs --from 'mcp-workers-bridge[cookies]' \
    chrome-storage-state reddit --profile "Default" --stdout
)"
```

### Use the published PyPI package

Run the server without cloning:

```bash
uvx mcp-workers-bridge
```

Or install it persistently:

```bash
uv tool install mcp-workers-bridge
# or
pipx install mcp-workers-bridge
```

The package page is
[pypi.org/project/mcp-workers-bridge](https://pypi.org/project/mcp-workers-bridge/).
To download the wheel and source archive without installing, use **Download
files** on that page, or:

```bash
python -m pip download mcp-workers-bridge --no-deps -d dist
```

Versions before 0.6.0 do not include the `chrome-storage-state` exporter. For
a repository checkout, run `uv sync --system-certs --extra cookies` and replace
each `uvx ... chrome-storage-state` command above with
`.venv/bin/chrome-storage-state`.

`Default` is Chrome's first profile. The directory name depends on the person
and browser setup; list `~/Library/Application Support/Google/Chrome/` and pass
the profile that is signed in (for example `--profile "Profile 1"`).

The exporter reads the "Chrome Safe Storage" key from Keychain (macOS may ask
for permission once), copies the cookie database to a temporary directory,
decrypts matching cookies, and prints storage-state JSON with `--stdout`. Use
`--output PATH` instead if you prefer a private mode-600 file. `--chrome-root`
can point at a non-default Chrome installation.

### Option B: Chrome DevTools (any platform)

1. Open Chrome, then **View > Developer > Developer Tools**.
2. Open the **Application** tab, then **Storage > Cookies**.
3. Select `https://www.youtube.com`, `https://x.com`, or
   `https://old.reddit.com`.
4. Copy the required names and values into a JSON object for the environment
   variable in step 2:

```json
{
  "cookies": [
    {
      "name": "SAPISID",
      "value": "<value>",
      "domain": ".youtube.com",
      "path": "/",
      "expires": -1,
      "secure": true
    }
  ],
  "origins": []
}
```

Required cookies per service:

| Service | Required cookie(s) | Export all cookies for |
| --- | --- | --- |
| YouTube history | `SAPISID` or `__Secure-3PAPISID` | `youtube.com` |
| X | `auth_token` and `ct0` | `x.com` |
| Reddit | `reddit_session` | `reddit.com` |

YouTube captions for public videos do not need a session.

## 2. Set the environment variables

Direct mode needs only the storage-state variable for each service you use:

| Data | Minimum variable |
| --- | --- |
| YouTube history | `YOUTUBE_STORAGE_STATE_JSON` |
| X | `X_STORAGE_STATE_JSON` |
| Reddit | `REDDIT_STORAGE_STATE_JSON` |
| YouTube captions only | none |

If you used the exporter in step 1, the variables are already set for that
shell; rerun those export commands in a new shell. If you used DevTools, assign
the JSON objects directly:

```bash
export YOUTUBE_STORAGE_STATE_JSON='{"cookies":[...],"origins":[]}'
export X_STORAGE_STATE_JSON='{"cookies":[...],"origins":[]}'
export REDDIT_STORAGE_STATE_JSON='{"cookies":[...],"origins":[]}'
```

## 3. Register with Codex CLI

### Option A: use the project config

Trust this checkout in `~/.codex/config.toml`:

```toml
[projects."/path/to/mcp-workers-bridge"]
trust_level = "trusted"
```

Then run `codex` from anywhere inside the checkout. The project-scoped
`.codex/config.toml` defines the MCP server entry and forwards the variables
listed above.

### Option B: register a user-level server

Recommended PyPI registration:

```bash
codex mcp add workers-bridge -- uvx mcp-workers-bridge
```

Codex stores `uvx` as the command with `mcp-workers-bridge` as its argument and
runs the published package on demand.

For a repository install instead:

```bash
cd /path/to/mcp-workers-bridge
codex mcp add workers-bridge -- "$(pwd)/.venv/bin/mcp-workers-bridge"
```

Add the environment pass-through list to the entry in `~/.codex/config.toml`:

```toml
[mcp_servers.workers-bridge]
command = "uvx"
args = ["mcp-workers-bridge"]
env_vars = [
  "YOUTUBE_STORAGE_STATE_JSON",
  "YOUTUBE_TIMEZONE",
  "YOUTUBE_CACHE_DIR",
  "X_STORAGE_STATE_JSON",
  "REDDIT_STORAGE_STATE_JSON",
  "UV_SYSTEM_CERTS"
]
```

For a repository install, use the repository `.venv` path as `command`.

Restart Codex after changing the config, then verify:

```bash
codex mcp list
codex mcp get workers-bridge
```

## 4. Verify the connection

In a Codex CLI session, run `/mcp` and confirm the server is connected. Then:

1. Call `bridge_configuration` and confirm `mode` is `direct`.
2. Call `workers_youtube_captions` with `"dQw4w9WgXcQ"` (no session needed).
3. Call `workers_youtube_history` with `refresh=false` before the first
   refresh, then with `refresh=true`.
4. Call `workers_x_home` and `workers_reddit_identity`.

Missing sessions produce a per-tool configuration error; they do not prevent
the server from starting.

## Documentation

- [PyPI package](https://pypi.org/project/mcp-workers-bridge/): published
  server package used by `uvx mcp-workers-bridge`.
- [docs/manual.md](docs/manual.md): full configuration, tool reference, cache,
  acceptance results, and security boundaries.
- [docs/codex-cloud.md](docs/codex-cloud.md): Codex Cloud setup, secrets, OIDC,
  and S3 permissions.
- [infra/service-bridge-workers/README.md](infra/service-bridge-workers/README.md):
  Worker deployment, Terraform, Access, and acceptance runbook.
- [infra/service-bridge-workers/API.md](infra/service-bridge-workers/API.md):
  HTTPS contract.
