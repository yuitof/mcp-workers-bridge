# Codex Cloud setup

This repository includes a project-scoped MCP definition at
`.codex/config.toml`. Codex Cloud must load the repository as a trusted project
and complete the setup phase before the stdio MCP process starts.

## Environment setup script

Set the Cloud environment's install script to:

```bash
bash mcp-workers-bridge/.codex/setup.sh
```

Run setup with elevated sandbox permissions. It writes outside the checkout:
the package-manager caches and `$CODEX_HOME/config.toml`, which carries the
project trust entry and MCP server definition. A sandbox limited to workspace
writes cannot complete those writes. When an agent runs it, use
`exec_command` with `sandbox_permissions: "require_escalated"` and a
justification naming the out-of-workspace writes. In Codex Cloud the
install-script phase itself runs inside the environment container. If a setup
run fails, retry it once with `sandbox_permissions: "require_escalated"`
before treating the failure as a blocker.

The script installs the locked Python environment and Worker npm dependencies.
It does not collect history, read cookies, invoke the Worker, or deploy
infrastructure. It also injects a `[projects."<checkout-path>"] trust_level =
"trusted"` entry and the repository MCP table into the persistent Cloud Codex
home, because project-scoped `.codex/config.toml` files are ignored for
untrusted projects.

The MCP launcher starts the server directly from the checkout. It resolves the
repository with `git rev-parse --show-toplevel` and falls back to `$PWD` and
`$PWD/mcp-workers-bridge`, so it still finds the checkout when Codex runs from
the parent directory. It does not fetch or fast-forward `origin/main`, and
`.codex/setup.sh` is not run by the launcher. Pull latest code and run setup
manually when dependencies or project trust need to be updated. Bootstrap
output is written to stderr so it cannot interfere with the MCP JSON stream.
Codex sends its normal MCP initialization request after the launcher transfers
control to `.venv/bin/mcp-workers-bridge`.

Setup injects the trust entry and the project MCP table into the user-level
`$CODEX_HOME/config.toml` under the marker
`# mcp-workers-bridge (injected by .codex/setup.sh)`. When the marker is
present, everything from the marker to the end of the file is replaced with
the current trust entry and repository configuration, so updates (including a
moved checkout path) overwrite the previous block instead of duplicating it.
When the marker is absent, the current trust entry and configuration are
appended after it. The injected server definition resolves
`.venv/bin/mcp-workers-bridge` to the absolute checkout path, while the
checked-in project config keeps the portable candidate search for local CLI
use. Content before the marker is never modified.

Setup uses a writable cache under `/tmp` for uv, uv-managed Python/tool files,
npm, XDG state, and Wrangler logs. This avoids failures when Cloud mounts the
home-directory cache or `$HOME/.config` read-only. Set
`MCP_WORKERS_BRIDGE_SETUP_CACHE` to another writable absolute directory if
`/tmp` is unavailable in your environment.

The `Makefile` targets default the same state to `.cache/workers` in the
checkout when the variables are unset, so `make install`, `make worker-build`,
and `make check` (including its `uv lock --check` step) also work when
`$HOME/.config`, the default uv cache, and the default npm cache are read-only.
For Wrangler commands run outside make, export `XDG_CONFIG_HOME` and
`WRANGLER_LOG_PATH` to writable paths. `make check` does not include
`terraform-check`; Terraform is only needed for `make terraform-check` and the
deployment runbook.

## Environment variables

Codex Cloud setup must fill `SERVICE_BRIDGE_WORKER_URL`,
`CF_ACCESS_CLIENT_ID`, and `CF_ACCESS_CLIENT_SECRET` before publishing the
environment. Ask the user for any missing value; never generate, echo, or
commit the Access secrets.

Add these ordinary environment variables:

```text
SERVICE_BRIDGE_WORKER_URL=https://service-bridge.<account-subdomain>.workers.dev
YOUTUBE_TIMEZONE=Europe/London
YOUTUBE_CACHE_DIR=/tmp/workers-bridge/youtube
YOUTUBE_CACHE_BUCKET=english-vault-youtube-cache-cachebucket-giafchlzhpuf
AWS_REGION=us-west-1
```

`YOUTUBE_CACHE_BUCKET` is optional. If S3 access is unavailable, history can
still use the task-local cache, and Workers MCP calls can explicitly pass
`use_cache=false`.

Codex Cloud's AWS OIDC integration may expose a selected `AWS_PROFILE` whose
definition lives in files referenced by `AWS_CONFIG_FILE` and
`AWS_SHARED_CREDENTIALS_FILE`. All three variables must be forwarded to the MCP
child process; forwarding only `AWS_PROFILE` causes boto3 to report that the
profile cannot be found. Do not copy the contents of those files into the
repository or environment documentation.

## Private values

Add the Cloudflare Access pair as HTTP secrets, each allowed for:

```text
service-bridge.<account-subdomain>.workers.dev
```

```text
CF_ACCESS_CLIENT_ID
CF_ACCESS_CLIENT_SECRET
```

The values are the sensitive Terraform outputs
`service_token_client_id` and `service_token_client_secret`. Do not store them
in the repository or use the same values for unrelated HTTP services.

Add collector sessions as personal environment variables because the MCP
process must read and scope the cookies itself:

```text
YOUTUBE_STORAGE_STATE_JSON
X_STORAGE_STATE_JSON
REDDIT_STORAGE_STATE_JSON
```

Only add sessions for collectors you intend to use. Missing sessions produce
structured MCP configuration errors for the corresponding tools.

## S3 access

Attach an AWS OIDC connection to the Cloud environment with read/write access
to:

```text
arn:aws:s3:::english-vault-youtube-cache-cachebucket-giafchlzhpuf
arn:aws:s3:::english-vault-youtube-cache-cachebucket-giafchlzhpuf/*
```

Allow these destinations when restricted internet access is enabled:

```text
sts.us-west-1.amazonaws.com
s3.us-west-1.amazonaws.com
english-vault-youtube-cache-cachebucket-giafchlzhpuf.s3.us-west-1.amazonaws.com
```

Enable the package-manager preset so setup can install Python and npm
dependencies.

## Publish and verify

Save and republish the Cloud environment, then start a new task. Inside the
task:

1. Run `/mcp`.
2. Confirm `workers-bridge-cloud` is connected.
3. Call `bridge_configuration`.
4. Call `workers_bridge_probe`.
5. For a cache-only check, call `workers_youtube_history` with
   `refresh=false`.

If the project-scoped stdio server does not appear, verify that the repository
is trusted and inspect the Cloud setup output for `.codex/setup.sh`. Current
Codex documentation warns that local MCP servers and transports may not be
available on every Cloud execution path; in that case this local-process MCP
cannot be attached through Cloud settings alone. Use local Codex or a
streamable-HTTP MCP deployment instead.

If setup fails while writing `config.toml`, the persistent Codex home is
mounted read-only. In that case the environment must provision the
`[projects."/absolute/checkout/path"] trust_level = "trusted"` entry before the
task starts, or expose the MCP through a supported remote transport. The
repository setup script cannot change a read-only mount.
