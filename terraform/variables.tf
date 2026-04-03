variable "cloudflare_api_token" {
  description = "Cloudflare API token with Workers/Pages/KV permissions"
  type        = string
  sensitive   = true
}

variable "cloudflare_account_id" {
  description = "Cloudflare account ID"
  type        = string
}

variable "pages_project_name" {
  description = "Cloudflare Pages project name"
  type        = string
  default     = "homeassistant-fe-tesla-image-uploader"
}

variable "worker_name" {
  description = "Cloudflare Worker script name"
  type        = string
  default     = "tesla-image-uploader-api"
}

variable "github_repo" {
  description = "GitHub repository (owner/name)"
  type        = string
  default     = "ds2000/homeassistant-fe-tesla-image-uploader"
}

variable "production_branch" {
  description = "Branch deployed to Pages production"
  type        = string
  default     = "TESUPL0001"
}
