# Public production settings, automatically loaded by Terraform. No secrets here.
project_id  = "simple-unmark-prod"
region      = "europe-west1"
zone        = "europe-west1-b"
domain_name = "confidential.simpleunmark.com"

# Set the NEW Secret Manager-capable release digest before planning.
# The previous f18c08cc image cannot use this credential bootstrap.
image_reference = "europe-west1-docker.pkg.dev/simple-unmark-prod/workloads/simpleunmark-confidential@sha256:d2470f946ea4856d30d6de9923112ea7da5a6110aedb8e9b526216e9fe9855b9"

# Payloads are uploaded directly to Secret Manager, not Terraform.
deepinfra_secret_version = "1"
shared_secret_version    = "1"
