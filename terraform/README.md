# Terraform — Cloudflare Infrastructure

Manages the Cloudflare Pages project and Worker for the Tesla Card Image Uploader.

## Resources

| Resource | Purpose |
|----------|---------|
| `cloudflare_pages_project.site` | Static site (`docs/`) on Pages |
| `cloudflare_workers_script.api` | API proxy Worker (dispatch, github, status) |
| `cloudflare_workers_kv_namespace.rate_limit` | KV for `/dispatch` rate limiting |

## Setup

```bash
cp terraform.tfvars.example terraform.tfvars
# Fill in cloudflare_api_token and cloudflare_account_id

terraform init
terraform plan
terraform apply
```

## Worker Secrets

Worker secrets are **never** in Terraform state (public repo). Set them via:

```bash
cd worker/
wrangler secret put GITHUB_PAT --name tesla-image-uploader-api
wrangler secret put GITHUB_UPLOAD_PAT --name tesla-image-uploader-api
```

## CI/CD

- **Worker deploys** automatically on push to `worker/` via `.github/workflows/deploy-worker.yml`
- **Pages deploys** automatically via Cloudflare's GitHub integration (configured in the Pages project)
- **GitHub Actions secrets required**: `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`

## Import Existing Resources

If the Pages project and Worker already exist, import them before applying:

```bash
terraform import cloudflare_pages_project.site <account_id>/<project_name>
terraform import cloudflare_workers_script.api <account_id>/<script_name>
terraform import cloudflare_workers_kv_namespace.rate_limit <account_id>/<namespace_id>
```
