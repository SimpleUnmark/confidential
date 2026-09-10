mock_provider "google" {}

run "deploy_only_the_reviewed_digest" {
  command = plan
  assert {
    condition     = google_compute_instance.workload.metadata["tee-image-reference"] == "europe-west1-docker.pkg.dev/simple-unmark-prod/workloads/simpleunmark-confidential@${local.release_policy.deploymentDigest}"
    error_message = "The VM must use the reviewed policy, not a mutable tag or environment override."
  }
  assert {
    condition     = google_compute_instance.workload.metadata["tee-container-log-redirect"] == "false"
    error_message = "Publishing changes must not enable workload logging."
  }
}
