# Public production settings, automatically loaded by Terraform. No secrets here.
project_id  = "simple-unmark-prod"
region      = "europe-west1"
zone        = "europe-west1-b"
domain_name = "confidential.simpleunmark.com"

# Image selection lives in releases/approved-workloads.json, not an override.

# Payloads are uploaded directly to Secret Manager, not Terraform.
deepinfra_secret_version = "1"
shared_secret_version    = "1"
