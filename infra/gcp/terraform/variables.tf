variable "project_id" {
  description = "GCP project that owns the Confidential Space VM and load balancer."
  type        = string
}

variable "region" {
  description = "GCP region for the subnet, router, and Cloud NAT."
  type        = string
  default     = "europe-west3"
}

variable "zone" {
  description = "Zone that supports N2D Confidential Space instances."
  type        = string
  default     = "europe-west3-b"
}

variable "name" {
  description = "Prefix for all Simple Unmark workload resources."
  type        = string
  default     = "simpleunmark-confidential-v1"
}

variable "domain_name" {
  description = "Public DNS name for the confidential workload, managed as a Cloudflare A record."
  type        = string
}

variable "repository_id" {
  description = "Artifact Registry repository managed by ../foundation."
  type        = string
  default     = "workloads"
}

variable "cloudflare_zone_id" {
  description = "Existing Cloudflare zone ID for simpleunmark.com. The zone itself is not created or changed."
  type        = string
}

variable "image_reference" {
  description = "Artifact Registry OCI image pinned by digest, for example europe-west3-docker.pkg.dev/project/repo/image@sha256:..."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9-]+-docker\\.pkg\\.dev/.+@sha256:[a-f0-9]{64}$", var.image_reference))
    error_message = "image_reference must be an Artifact Registry reference pinned by a sha256 digest."
  }
}

variable "deepinfra_api_key" {
  description = "DeepInfra API key stored in ordinary instance metadata. This sensitive value is also stored in Terraform state."
  type        = string
  sensitive   = true
}

variable "confidential_shared_secret" {
  description = "HMAC key shared with the web app, stored in ordinary instance metadata. Generate at least 32 random bytes."
  type        = string
  sensitive   = true

  validation {
    condition     = length(var.confidential_shared_secret) >= 32
    error_message = "confidential_shared_secret must contain at least 32 characters."
  }
}

variable "machine_type" {
  description = "N2D machine type for the single v1 workload."
  type        = string
  default     = "n2d-standard-2"
}

variable "subnet_cidr" {
  description = "Private IPv4 range for the workload subnet."
  type        = string
  default     = "10.87.0.0/24"
}

variable "deletion_protection" {
  description = "Protect the workload VM from deletion. Keep false for automated digest-pinned payload replacement; enabling it deliberately blocks image rotations until disabled."
  type        = bool
  default     = false
}

variable "labels" {
  description = "Additional resource labels."
  type        = map(string)
  default     = {}
}
