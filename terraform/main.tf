provider "cloudflare" {
  api_token = var.cloudflare_api_token
}

# ── KV namespace for Worker rate limiting ────────────────────
resource "cloudflare_workers_kv_namespace" "rate_limit" {
  account_id = var.cloudflare_account_id
  title      = "${var.worker_name}-rate-limit"
}

# ── Worker script ────────────────────────────────────────────
# Secrets (GITHUB_PAT, GITHUB_UPLOAD_PAT) are set out-of-band
# via `wrangler secret put` — never stored in Terraform state.
resource "cloudflare_workers_script" "api" {
  account_id  = var.cloudflare_account_id
  script_name = var.worker_name
  content     = file("${path.module}/../worker/index.js")
  main_module = "index.js"

  compatibility_date  = "2024-01-01"

  bindings = [
    {
      type          = "kv_namespace"
      name          = "RATE_LIMIT"
      namespace_id  = cloudflare_workers_kv_namespace.rate_limit.id
    },
  ]
}

# Worker subdomain route (*.workers.dev)
resource "cloudflare_workers_subdomain" "api" {
  account_id = var.cloudflare_account_id
  subdomain  = "david-c22"
}

# ── Pages project ────────────────────────────────────────────
resource "cloudflare_pages_project" "site" {
  account_id = var.cloudflare_account_id
  name       = var.pages_project_name

  production_branch = var.production_branch

  source = {
    type = "github"
    config = {
      owner             = split("/", var.github_repo)[0]
      repo_name         = split("/", var.github_repo)[1]
      production_branch = var.production_branch
    }
  }

  build_config = {
    build_command   = ""
    destination_dir = "docs"
  }

  deployment_configs = {
    production = {
      compatibility_date = "2025-09-27"
    }
    preview = {
      compatibility_date = "2025-09-27"
    }
  }
}
