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
  default = "europe-west3"
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
  ])
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

resource "google_artifact_registry_repository" "workload" {
  project       = var.project_id
  location      = var.region
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
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.workload.repository_id}/simpleunmark-confidential"
}
