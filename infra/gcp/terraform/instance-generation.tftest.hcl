mock_provider "google" {}

run "first_generation" {
  command = plan
  override_resource {
    override_during = plan
    target          = terraform_data.workload_image
    values          = { id = "11111111-1111-4111-8111-111111111111" }
  }
  assert {
    condition     = google_compute_instance.workload.name == "simpleunmark-confidential-v1-11111111-111"
    error_message = "VM names must include the release generation rather than reusing the same URL."
  }
}

run "replacement_generation" {
  command = plan
  override_resource {
    override_during = plan
    target          = terraform_data.workload_image
    values          = { id = "22222222-2222-4222-8222-222222222222" }
  }
  assert {
    condition     = google_compute_instance.workload.name == "simpleunmark-confidential-v1-22222222-222"
    error_message = "A new release generation must change the VM name and its membership URL."
  }
}
