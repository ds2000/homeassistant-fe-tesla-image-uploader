output "pages_url" {
  description = "Cloudflare Pages production URL"
  value       = "https://${cloudflare_pages_project.site.name}.pages.dev"
}

output "worker_url" {
  description = "Cloudflare Worker URL"
  value       = "https://${var.worker_name}.david-c22.workers.dev"
}

output "kv_namespace_id" {
  description = "KV namespace ID for rate limiting"
  value       = cloudflare_workers_kv_namespace.rate_limit.id
}
