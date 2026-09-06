# Public production settings, automatically loaded by Terraform. No secrets here.
project_id  = "simple-unmark-prod"
region      = "europe-west1"
zone        = "europe-west1-b"
domain_name = "confidential.simpleunmark.com"

# Set the NEW Secret Manager-capable release digest before planning.
# The previous f18c08cc image cannot use this credential bootstrap.
image_reference = "europe-west1-docker.pkg.dev/simple-unmark-prod/workloads/simpleunmark-confidential@sha256:REPLACE_WITH_NEW_RELEASE_DIGEST"

# Payloads are uploaded directly to Secret Manager, not Terraform.
deepinfra_secret_version = "1"
shared_secret_version    = "1"
