# Numeric IDs prevent a renamed/deleted GitHub namespace being claimed by a
# different repository. No service-account keys or Terraform-managed secrets.
variable "github_repository_id" {
  type    = string
  default = "1358635275"
}

variable "github_owner_id" {
  type    = string
  default = "325450359"
}

resource "google_iam_workload_identity_pool" "github" {
  project                   = var.project_id
  workload_identity_pool_id = "github-confidential-releases"
  display_name              = "Confidential release CI"
  depends_on                = [google_project_service.required]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github"
  display_name                       = "Reviewed main releases only"

  attribute_mapping = {
    "google.subject"                = "assertion.sub"
    "attribute.repository_id"       = "assertion.repository_id"
    "attribute.repository_owner_id" = "assertion.repository_owner_id"
    "attribute.ref"                 = "assertion.ref"
    "attribute.environment"         = "assertion.environment"
    "attribute.workflow_ref"        = "assertion.workflow_ref"
    "attribute.event_name"          = "assertion.event_name"
    "attribute.runner_environment"  = "assertion.runner_environment"
  }
  attribute_condition = join(" && ", [
    "assertion.repository_owner_id == '${var.github_owner_id}'",
    "assertion.repository_id == '${var.github_repository_id}'",
    "assertion.ref == 'refs/heads/main'",
    "assertion.environment == 'production'",
    "assertion.workflow_ref == 'SimpleUnmark/confidential/.github/workflows/release-confidential.yml@refs/heads/main'",
    "assertion.event_name == 'workflow_dispatch'",
    "assertion.runner_environment == 'github-hosted'",
  ])
  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account" "image_publisher" {
  project      = var.project_id
  account_id   = "confidential-image-publisher"
  display_name = "GitHub workload image publisher (no deploy or secret access)"
  depends_on   = [google_project_service.required]
}

resource "google_service_account_iam_member" "github_publisher" {
  service_account_id = google_service_account.image_publisher.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository_id/${var.github_repository_id}"
}

resource "google_artifact_registry_repository_iam_member" "image_publisher" {
  project    = var.project_id
  location   = var.region
  repository = google_artifact_registry_repository.workload[var.region].repository_id
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.image_publisher.email}"
}

# Applies only to the active workload repository, never project-wide. All images
# in this repository become public, not just a particular tag. Do not store any
# private application images or credentials here.
resource "google_artifact_registry_repository_iam_member" "public_reader" {
  project    = var.project_id
  location   = var.region
  repository = google_artifact_registry_repository.workload[var.region].repository_id
  role       = "roles/artifactregistry.reader"
  member     = "allUsers"

  # The organization otherwise rejects allUsers. API ordering is enforced here;
  # Google policy propagation can still require a later retry of the apply.
  depends_on = [google_org_policy_policy.external_members]
}

output "github_publishing_variables" {
  description = "Non-secret inputs consumed by the GitHub Terraform stack."
  value = {
    GCP_PROJECT_ID                 = var.project_id
    GCP_WORKLOAD_IDENTITY_PROVIDER = google_iam_workload_identity_pool_provider.github.name
    GCP_PUBLISHER_SERVICE_ACCOUNT  = google_service_account.image_publisher.email
    GCP_IMAGE                      = "${var.region}-docker.pkg.dev/${var.project_id}/${var.repository_id}/simpleunmark-confidential"
  }
}
