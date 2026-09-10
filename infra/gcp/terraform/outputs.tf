output "public_ip" {
  description = "Load balancer IPv4 address. Manually point the workload hostname's DNS-only A record here."
  value       = google_compute_global_address.workload.address
}

output "confidential_server_public_url" {
  description = "Set CONFIDENTIAL_SERVER_PUBLIC_URL in the web deployment to this value."
  value       = "https://${var.domain_name}"
}

output "workload_service_account" {
  description = "Include this exact identity in NEXT_PUBLIC_CONFIDENTIAL_EXPECTED_SERVICE_ACCOUNTS."
  value       = google_service_account.workload.email
}

output "project_number" {
  description = "Include this value in NEXT_PUBLIC_CONFIDENTIAL_EXPECTED_GCP_PROJECT_NUMBERS."
  value       = data.google_project.current.number
}

output "expected_image_digest" {
  description = "Deployment digest selected by releases/approved-workloads.json; export that policy to the website before rotating the VM."
  value       = local.release_policy.deploymentDigest
}

data "google_project" "current" {
  project_id = var.project_id
}
