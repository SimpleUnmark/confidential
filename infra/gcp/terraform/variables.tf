variable "project_id" {
  description = "GCP project that owns the Confidential Space VM and load balancer."
  type        = string
}

variable "region" {
  description = "GCP region for the subnet, router, and Cloud NAT."
  type        = string
  default     = "europe-west1"
}

variable "zone" {
  description = "Zone that supports N2D Confidential Space instances."
  type        = string
  default     = "europe-west1-b"
}

variable "name" {
  description = "Prefix for all Simple Unmark workload resources."
  type        = string
  default     = "simpleunmark-confidential-v1"
}

variable "domain_name" {
  description = "Public hostname for the HTTPS certificate. The operator manages its DNS record separately."
  type        = string
}

variable "repository_id" {
  description = "Artifact Registry repository managed by ../foundation."
  type        = string
  default     = "workloads"
}

variable "image_reference" {
  description = "Artifact Registry OCI image pinned by digest, for example europe-west1-docker.pkg.dev/project/repo/image@sha256:..."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9-]+-docker\\.pkg\\.dev/.+@sha256:[a-f0-9]{64}$", var.image_reference))
    error_message = "image_reference must be an Artifact Registry reference pinned by a sha256 digest."
  }
}

variable "deepinfra_secret_version" {
  description = "Numeric Secret Manager version ID; never the credential value."
  type        = string
  default     = "1"

  validation {
    condition     = can(regex("^[1-9][0-9]*$", var.deepinfra_secret_version))
    error_message = "Pin an explicit positive version number, not latest."
  }
}

variable "shared_secret_version" {
  description = "Numeric version of the HMAC key also configured in the website backend."
  type        = string
  default     = "1"

  validation {
    condition     = can(regex("^[1-9][0-9]*$", var.shared_secret_version))
    error_message = "Pin an explicit positive version number, not latest."
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
