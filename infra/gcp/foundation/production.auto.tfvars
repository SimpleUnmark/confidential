# Public production settings, automatically loaded by Terraform. No secrets here.
project_id = "simple-unmark-prod"
region     = "europe-west1"

# Preserve the deployed Frankfurt repository and its images during cutover.
retained_repository_regions = ["europe-west3"]
