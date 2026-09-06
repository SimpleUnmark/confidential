terraform {
  required_version = "= 1.15.8"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 7.45"
    }
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 5.15"
    }
  }
}

# Uses CLOUDFLARE_API_TOKEN from the operator's environment.
provider "cloudflare" {}

provider "google" {
  project = var.project_id
  region  = var.region
  zone    = var.zone
}
