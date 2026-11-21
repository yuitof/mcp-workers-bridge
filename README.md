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

The `cookies` extra enables automatic Chrome extraction. Codex runs the
published package on demand.

Environment variables must be declared inside the Codex session: `env_vars`
only forwards variables that the Codex process inherited at startup. Exports
from a separate terminal, or added after Codex started, are not visible to the
MCP server. Restart Codex after changing the config or environment.

## 2. Extract cookies and set the environment variables

Cookies are full account credentials. Keep the values private, never commit
them, and never paste them into chat.

Declare these variables inside the Codex session, in the same terminal or app
environment that launches it:

The variables are optional. Cookie-backed tools require a `profile` argument
such as `Default` or `Profile 1`. If the matching profile is absent from the
storage-state variable, the tool extracts that profile from Chrome on macOS and
caches the cookies in the MCP process. This requires `mcp-workers-bridge`
0.7.0 or newer.

One storage-state variable can hold several profiles:

```json
{
  "profiles": {
    "Default": {"cookies": [...]},
    "Profile 1": {"cookies": [...]}
  }
}
```

Call `clear_cookie_state` to remove selected cookies from the running MCP:

```json
{"service": "x", "profile": "Profile 1"}
```

Omit `service` or `profile` to clear more broadly. Clearing is process-local;
restarting Codex restores values inherited from its environment.

Clear cache artifacts with `clear_cache`:

```json
{"target": "both", "service": "youtube"}
```

`target` accepts `local`, `s3`, or `both`. Local clearing removes
`history.json` and `transcripts/` but leaves `auth/` untouched; S3 clearing
removes only the `youtube/` prefix.

Direct mode needs only the storage-state variable for each service you use:

| Data | Minimum variable |
| --- | --- |
| YouTube history | `YOUTUBE_STORAGE_STATE_JSON` |
| X | `X_STORAGE_STATE_JSON` |
| Reddit | `REDDIT_STORAGE_STATE_JSON` |
| YouTube captions only | none |

### Bundled exporter (macOS)

Extract each service directly into its environment variable with uvx. This
requires `mcp-workers-bridge` 0.7.0 or newer:

```bash
export YOUTUBE_STORAGE_STATE_JSON="$(
  uvx --system-certs --from mcp-workers-bridge \
    chrome-storage-state youtube --profile "Default" --stdout
)"
export X_STORAGE_STATE_JSON="$(
  uvx --system-certs --from mcp-workers-bridge \
    chrome-storage-state x --profile "Default" --stdout
)"
export REDDIT_STORAGE_STATE_JSON="$(
  uvx --system-certs --from mcp-workers-bridge \
    chrome-storage-state reddit --profile "Default" --stdout
)"
```

## 3. Verify the connection

In a Codex CLI session, run `/mcp` and confirm the server is connected. Then:

1. Call `bridge_configuration` and confirm `mode` is `direct`.
2. Call `workers_youtube_captions` with `"dQw4w9WgXcQ"` (no session needed).
3. Call `workers_youtube_history` with `profile: "Default"` and
   `refresh=false` before the first refresh, then with `refresh=true`.
4. Call `workers_x_home` and `workers_reddit_identity` with
   `profile: "Default"`.

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
