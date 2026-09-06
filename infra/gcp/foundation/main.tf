terraform {
  required_version = "= 1.15.8"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 7.45"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

variable "project_id" {
  description = "Existing GCP project with billing enabled."
  type        = string
}

variable "region" {
  type    = string
  default = "europe-west1"
}

variable "retained_repository_regions" {
  description = "Previous registry locations retained for rollback during a regional migration. Removing one requires a separate, reviewed retirement."
  type        = set(string)
  default     = []
}

variable "repository_id" {
  type    = string
  default = "workloads"
}

resource "google_project_service" "required" {
  for_each = toset([
    "artifactregistry.googleapis.com",
    "compute.googleapis.com",
    "confidentialcomputing.googleapis.com",
    "iam.googleapis.com",
    "secretmanager.googleapis.com",
  ])
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

# Adopt the already-deployed Frankfurt registry without replacing it. Keep this
# declaration so checkouts using the original state can upgrade safely.
moved {
  from = google_artifact_registry_repository.workload
  to   = google_artifact_registry_repository.workload["europe-west3"]
}

resource "google_artifact_registry_repository" "workload" {
  for_each = setunion(toset([var.region]), var.retained_repository_regions)

  project       = var.project_id
  location      = each.key
  repository_id = var.repository_id
  description   = "Simple Unmark confidential workload images"
  format        = "DOCKER"
  labels = {
    application = "simpleunmark"
    managed-by  = "terraform"
  }

  lifecycle {
    prevent_destroy = true
  }
  depends_on = [google_project_service.required]
}

output "image_repository" {
  description = "Publish images here, then pass the immutable digest to the runtime stack."
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.workload[var.region].repository_id}/simpleunmark-confidential"
}

output "retained_image_repositories" {
  description = "Previous registries kept intact for rollback; do not publish new releases here."
  value = {
    for region, repository in google_artifact_registry_repository.workload :
    region => "${region}-docker.pkg.dev/${var.project_id}/${repository.repository_id}/simpleunmark-confidential"
    if region != var.region
  }
}

# Containers only: operators upload values directly, never through Terraform.
resource "google_secret_manager_secret" "workload" {
  for_each  = toset(["simpleunmark-deepinfra-api-key", "simpleunmark-confidential-shared-secret"])
  project   = var.project_id
  secret_id = each.key

  replication {
    user_managed {
      replicas {
        location = var.region
      }
    }
  }

  labels = {
    application = "simpleunmark"
    managed-by  = "terraform"
  }

  lifecycle {
    prevent_destroy = true
  }
  depends_on = [google_project_service.required]
}

output "secret_resources" {
  description = "Secret containers only. Upload values directly, not through Terraform."
  value       = { for name, secret in google_secret_manager_secret.workload : name => secret.id }
}
