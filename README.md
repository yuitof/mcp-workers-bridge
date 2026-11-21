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

## 1. Register with Codex CLI

### Register a user-level server

Recommended PyPI registration:

```bash
codex mcp add workers-bridge -- uvx mcp-workers-bridge
```

Codex stores `uvx` as the command with `mcp-workers-bridge` as its argument and
runs the published package on demand.

Environment variables must be declared inside the Codex session: `env_vars`
only forwards variables that the Codex process inherited at startup. Exports
from a separate terminal, or added after Codex started, are not visible to the
MCP server. Restart Codex after changing the config or environment.

## 2. Extract cookies and set the environment variables

Cookies are full account credentials. Keep the values private, never commit
them, and never paste them into chat.

Declare these variables inside the Codex session, in the same terminal or app
environment that launches it:

Direct mode needs only the storage-state variable for each service you use:

| Data | Minimum variable |
| --- | --- |
| YouTube history | `YOUTUBE_STORAGE_STATE_JSON` |
| X | `X_STORAGE_STATE_JSON` |
| Reddit | `REDDIT_STORAGE_STATE_JSON` |
| YouTube captions only | none |

### Bundled exporter (macOS)

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

## 3. Verify the connection

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
