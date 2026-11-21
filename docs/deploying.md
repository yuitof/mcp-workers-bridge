# Deploying the Worker

Step-by-step deployment for `service-bridge`. The detailed operational
runbook remains in
[infra/service-bridge-workers/README.md](../infra/service-bridge-workers/README.md).

## 1. Create the Cloudflare API token

Use a scoped Cloudflare API token, not the legacy Global API Key. In the
Cloudflare dashboard:

1. Open **My Profile > API Tokens**.
2. Select **Create Token > Create Custom Token**.
3. Add these account permissions:

| Permission | Access | Used for |
| --- | --- | --- |
| Workers Scripts | Edit | Wrangler deployment |
| Access: Apps and Policies | Edit | Terraform Access application/policy |
| Access: Service Tokens | Edit | Terraform runtime service token |
| Account Settings | Read | Account/provider lookup |

Set **Account Resources** to the specific account that owns the Worker.
Restrict the token to the deployment window and rotate it afterward. For
day-to-day inspection, use a separate read-only token with Workers Scripts
Read and Access read permissions.

The Cloudflare API token is a control-plane credential for Wrangler and
Terraform. It is not the runtime `CF_ACCESS_CLIENT_ID` /
`CF_ACCESS_CLIENT_SECRET` pair. Terraform creates that service-token pair for
requests to the Worker.

## 2. Install dependencies

Use Node 22.12+, 24, or 26+ and Terraform 1.7+. From the repository root:

```bash
cd infra/service-bridge-workers

npm ci
npm run check
npm test
npm run build

terraform -chdir=terraform fmt -check
terraform -chdir=terraform init -backend=false
terraform -chdir=terraform validate
```

Export the account and deployment token in the deployment shell:

```bash
export CLOUDFLARE_ACCOUNT_ID="<account ID>"
export CLOUDFLARE_API_TOKEN="<scoped API token>"
npx wrangler whoami
```

Do not commit token values or put them in `wrangler.jsonc`, Terraform
variables, or this repository.

## 3. Deploy the Worker with Wrangler

Deploy the checked-in safe configuration first:

```bash
npx wrangler deploy
```

This uploads the Worker but leaves `workers_dev` and `preview_urls` disabled,
and keeps diagnostics, caption relay, and collector relay disabled. Record the
deployment ID privately.

Retrieve the stable Worker ID with a read-capable token:

```bash
npm run inspect
```

Keep that Worker ID for Terraform.

## 4. Create Access resources with Terraform

Keep state, plans, and account variables outside the repository:

```bash
export BRIDGE_STATE_DIR="$HOME/.private/service-bridge-workers"
mkdir -p "$BRIDGE_STATE_DIR"
chmod 700 "$BRIDGE_STATE_DIR"

cp terraform/account.tfvars.example "$BRIDGE_STATE_DIR/account.tfvars"
chmod 600 "$BRIDGE_STATE_DIR/account.tfvars"
```

Set `account_id` and `worker_id` in that private file. Generate and review the
plan:

```bash
terraform -chdir=terraform plan \
  -input=false \
  -state="$BRIDGE_STATE_DIR/terraform.tfstate" \
  -var-file="$BRIDGE_STATE_DIR/account.tfvars" \
  -out="$BRIDGE_STATE_DIR/access.tfplan"

terraform -chdir=terraform show \
  -no-color "$BRIDGE_STATE_DIR/access.tfplan"
```

The plan should create exactly:

- one seven-day Cloudflare Access service token;
- one self-hosted Access application that targets the Worker ID and allows
  that service token.

After reviewing the plan, apply it with a token that has Access write
permissions:

```bash
terraform -chdir=terraform apply \
  -input=false \
  -state="$BRIDGE_STATE_DIR/terraform.tfstate" \
  "$BRIDGE_STATE_DIR/access.tfplan"
```

Read the sensitive outputs directly into private configuration; do not print
them:

```bash
terraform -chdir=terraform output \
  -state="$BRIDGE_STATE_DIR/terraform.tfstate" \
  -raw service_token_client_id

terraform -chdir=terraform output \
  -state="$BRIDGE_STATE_DIR/terraform.tfstate" \
  -raw service_token_client_secret
```

## 5. Run acceptance checks

Create the private acceptance profile:

```bash
cp wrangler.jsonc wrangler.acceptance.local.jsonc
```

In that ignored file, enable the public surfaces while keeping all features
disabled:

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

Deploy it and test Access on both production and version preview URLs:

```bash
npx wrangler deploy --config wrangler.acceptance.local.jsonc

export SERVICE_BRIDGE_WORKER_URL="https://service-bridge.<subdomain>.workers.dev"
export SERVICE_BRIDGE_WORKER_PREVIEW_URL="<exact version preview URL>"
export CF_ACCESS_CLIENT_ID="<from Terraform output>"
export CF_ACCESS_CLIENT_SECRET="<from Terraform output>"

npm run acceptance -- access
```

Missing and invalid credentials must be denied; valid credentials must reach
the Worker. Then follow the runbook to enable diagnostics temporarily and run:

```bash
npm run acceptance -- egress
npm run acceptance -- captions
```

Restore diagnostics/previews to the disabled settings before leaving the
acceptance window. Enable `COMPARISON_ENABLED` or `RELAY_ENABLED` only when the
corresponding feature is intentionally exposed and Access has been verified.

## 6. Rollback

Redeploy the checked-in disabled configuration:

```bash
npx wrangler deploy
```

Keep the Access application and service token in place while shutting down.
After confirming the Worker is unreachable, remove them with a separately
reviewed Terraform plan. Remove private state and credentials according to
your retention policy.

## Troubleshooting permissions

- `403`/`Authentication error` from Wrangler: confirm the token has Workers
  Scripts Edit and the account resource is included.
- Terraform Access errors: confirm Access Apps and Policies Edit plus Access
  Service Tokens Edit.
- `npm run inspect` failure: use an account-scoped token with Workers Scripts
  Read and verify `CLOUDFLARE_ACCOUNT_ID`.
- Never use a Global API Key or commit a token; create a new scoped token if
  one is exposed.

See [environment.md](environment.md) for runtime variables and
[../infra/service-bridge-workers/API.md](../infra/service-bridge-workers/API.md)
for the HTTPS contract.
