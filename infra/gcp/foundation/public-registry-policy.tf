# Explicitly approved by the operator on 2026-09-11. This is a PROJECT-wide
# exception to domain-restricted sharing, not a repository-scoped exception.
# Other projects continue to inherit the organization's existing restriction.
# The exception permits external IAM grants; it does not itself grant access.

# Org Policy is a client-based API: local user ADC needs an explicit quota
# project. Scope this override to Org Policy so API bootstrap keeps its existing
# provider behavior. No credential files or service-account keys are needed.
provider "google" {
  alias                 = "org_policy"
  project               = var.project_id
  billing_project       = var.project_id
  user_project_override = true
}

data "google_project" "publishing" {
  project_id = var.project_id
}

resource "google_org_policy_policy" "external_members" {
  provider = google.org_policy
  name     = "projects/${data.google_project.publishing.number}/policies/iam.allowedPolicyMemberDomains"
  parent   = "projects/${data.google_project.publishing.number}"

  spec {
    inherit_from_parent = false
    rules {
      allow_all = "TRUE"
    }
  }

  lifecycle {
    precondition {
      condition     = var.project_id == "simple-unmark-prod"
      error_message = "The domain-sharing exception is authorized only for simple-unmark-prod. Review separately before using this stack for another project."
    }
  }

  depends_on = [google_project_service.required["orgpolicy.googleapis.com"]]
}
