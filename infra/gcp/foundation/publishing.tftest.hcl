mock_provider "google" {
  mock_data "google_project" {
    defaults = {
      number = "345268910027"
    }
  }
}

mock_provider "google" {
  alias = "org_policy"
}

run "publisher_is_repository_scoped" {
  command = plan
  assert {
    condition     = google_org_policy_policy.external_members.parent == "projects/345268910027" && google_org_policy_policy.external_members.name == "projects/345268910027/policies/iam.allowedPolicyMemberDomains"
    error_message = "The domain-sharing exception must affect only the production project, not the organization."
  }
  assert {
    condition     = google_org_policy_policy.external_members.spec[0].inherit_from_parent == false && google_org_policy_policy.external_members.spec[0].rules[0].allow_all == "TRUE" && length(google_org_policy_policy.external_members.spec[0].rules) == 1
    error_message = "The approved project override must explicitly replace the inherited domain restriction."
  }
  assert {
    condition     = google_artifact_registry_repository_iam_member.image_publisher.role == "roles/artifactregistry.writer" && google_artifact_registry_repository_iam_member.image_publisher.location == "europe-west1"
    error_message = "Publisher must be scoped to the active registry."
  }
  assert {
    condition     = google_artifact_registry_repository_iam_member.public_reader.member == "allUsers" && google_artifact_registry_repository_iam_member.public_reader.role == "roles/artifactregistry.reader" && google_artifact_registry_repository_iam_member.public_reader.location == "europe-west1"
    error_message = "Public read must target only the active registry."
  }
  assert {
    condition = alltrue([for constraint in [
      "assertion.repository_owner_id == '325450359'",
      "assertion.repository_id == '1358635275'",
      "assertion.ref == 'refs/heads/main'",
      "assertion.environment == 'production'",
      "assertion.event_name == 'workflow_dispatch'",
      "assertion.runner_environment == 'github-hosted'",
      "assertion.workflow_ref == 'SimpleUnmark/confidential/.github/workflows/release-confidential.yml@refs/heads/main'",
    ] : strcontains(google_iam_workload_identity_pool_provider.github.attribute_condition, constraint)])
    error_message = "The federation trust boundary must not broaden."
  }
}

run "reject_unapproved_project_exception" {
  command = plan
  variables {
    project_id = "another-project"
  }
  expect_failures = [google_org_policy_policy.external_members]
}
