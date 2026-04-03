output "pages_url" {
  description = "Cloudflare Pages production URL"
  value       = "https://${cloudflare_pages_project.site.name}.pages.dev"
}

output "worker_url" {
  description = "Cloudflare Worker URL"
  value       = "https://${var.worker_name}.${cloudflare_workers_subdomain.api.subdomain}.workers.dev"
}

output "kv_namespace_id" {
  description = "KV namespace ID for rate limiting"
  value       = cloudflare_workers_kv_namespace.rate_limit.id
}

output "pages_project_name" {
  description = "Pages project name (for deploy workflow)"
  value       = cloudflare_pages_project.site.name
}
