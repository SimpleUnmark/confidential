locals {
  release_policy  = jsondecode(file("${path.module}/../../../releases/approved-workloads.json"))
  image_reference = "${var.region}-docker.pkg.dev/${var.project_id}/${var.repository_id}/simpleunmark-confidential@${local.release_policy.deploymentDigest}"
  labels = merge({
    application  = "simpleunmark"
    component    = "confidential-workload"
    environment  = "production"
    "managed-by" = "terraform"
  }, var.labels)
}

# The Confidential Space launcher reads the payload image reference only at
# boot. Replacing this trigger therefore replaces the single stateless VM on an
# image-digest or secret-version change instead of retaining stale code/keys.
resource "terraform_data" "workload_image" {
  triggers_replace = [
    local.image_reference,
    var.deepinfra_secret_version,
    var.shared_secret_version,
  ]
}

# APIs and this registry are owned by ../foundation and applied first.
data "google_artifact_registry_repository" "workload" {
  project       = var.project_id
  location      = var.region
  repository_id = var.repository_id
}

resource "google_compute_network" "workload" {
  name                    = "${var.name}-network"
  auto_create_subnetworks = false

  depends_on = [data.google_artifact_registry_repository.workload]
}

resource "google_compute_subnetwork" "workload" {
  name                     = "${var.name}-subnet"
  ip_cidr_range            = var.subnet_cidr
  region                   = var.region
  network                  = google_compute_network.workload.id
  private_ip_google_access = true
}

resource "google_compute_router" "workload" {
  name    = "${var.name}-router"
  region  = var.region
  network = google_compute_network.workload.id
}

resource "google_compute_router_nat" "workload" {
  name                               = "${var.name}-nat"
  router                             = google_compute_router.workload.name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "LIST_OF_SUBNETWORKS"

  subnetwork {
    name                    = google_compute_subnetwork.workload.id
    source_ip_ranges_to_nat = ["ALL_IP_RANGES"]
  }
}

resource "google_service_account" "workload" {
  account_id   = substr(replace(var.name, "_", "-"), 0, 30)
  display_name = "Simple Unmark Confidential Space v1"

  depends_on = [data.google_artifact_registry_repository.workload]
}

resource "google_project_iam_member" "confidential_workload_user" {
  project = var.project_id
  role    = "roles/confidentialcomputing.workloadUser"
  member  = "serviceAccount:${google_service_account.workload.email}"
}

# Retain Confidential Space launcher diagnostics, including startup failures.
# This does not enable container stdout/stderr redirection: keep the VM's
# tee-container-log-redirect=false and the image's log_redirect=never policy.
resource "google_project_iam_member" "launcher_log_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.workload.email}"
}

resource "google_artifact_registry_repository_iam_member" "artifact_reader" {
  project    = var.project_id
  location   = var.region
  repository = data.google_artifact_registry_repository.workload.repository_id
  role       = "roles/artifactregistry.reader"
  member     = "serviceAccount:${google_service_account.workload.email}"
}

# Container metadata only: never read secret version payloads in Terraform.
data "google_secret_manager_secret" "workload" {
  for_each  = toset(["simpleunmark-deepinfra-api-key", "simpleunmark-confidential-shared-secret"])
  project   = var.project_id
  secret_id = each.key
}

resource "google_secret_manager_secret_iam_member" "workload_reader" {
  for_each  = data.google_secret_manager_secret.workload
  project   = var.project_id
  secret_id = each.value.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.workload.email}"
}

data "google_compute_image" "confidential_space" {
  project = "confidential-space-images"
  family  = "confidential-space"

  depends_on = [data.google_artifact_registry_repository.workload]
}

resource "google_compute_instance" "workload" {
  name                      = var.name
  zone                      = var.zone
  machine_type              = var.machine_type
  min_cpu_platform          = "AMD Milan"
  allow_stopping_for_update = true
  deletion_protection       = var.deletion_protection
  labels                    = local.labels
  tags                      = ["simpleunmark-confidential"]

  boot_disk {
    auto_delete = true

    initialize_params {
      image = data.google_compute_image.confidential_space.self_link
      size  = 12
      type  = "pd-balanced"
    }
  }

  confidential_instance_config {
    enable_confidential_compute = true
    confidential_instance_type  = "SEV"
  }

  scheduling {
    automatic_restart   = true
    on_host_maintenance = "MIGRATE"
    preemptible         = false
    provisioning_model  = "STANDARD"
  }

  shielded_instance_config {
    enable_integrity_monitoring = true
    enable_secure_boot          = true
    enable_vtpm                 = true
  }

  network_interface {
    subnetwork = google_compute_subnetwork.workload.id
    # Intentionally no access_config: the VM has no public IP.
  }

  service_account {
    email  = google_service_account.workload.email
    scopes = ["https://www.googleapis.com/auth/cloud-platform"]
  }

  metadata = {
    tee-image-reference                   = local.image_reference
    tee-restart-policy                    = "Always"
    tee-container-log-redirect            = "false"
    tee-mount                             = "type=tmpfs,source=tmpfs,destination=/tmp/simpleunmark,size=1073741824"
    simpleunmark-deepinfra-secret-version = "${data.google_secret_manager_secret.workload["simpleunmark-deepinfra-api-key"].id}/versions/${var.deepinfra_secret_version}"
    simpleunmark-shared-secret-version    = "${data.google_secret_manager_secret.workload["simpleunmark-confidential-shared-secret"].id}/versions/${var.shared_secret_version}"
    block-project-ssh-keys                = "true"
    enable-oslogin                        = "TRUE"
    serial-port-enable                    = "false"
  }

  lifecycle {
    replace_triggered_by = [terraform_data.workload_image]

    precondition {
      condition     = startswith(var.zone, "${var.region}-")
      error_message = "zone must belong to region."
    }
    precondition {
      condition = try(
        local.release_policy.schemaVersion == 1 &&
        local.release_policy.repository == "SimpleUnmark/confidential" &&
        local.release_policy.protocolVersion == 4 &&
        can(regex("^sha256:[a-f0-9]{64}$", local.release_policy.deploymentDigest)) &&
        length([for release in local.release_policy.workloads : release
          if release.digest == local.release_policy.deploymentDigest && release.status == "active"
        ]) == 1,
        false
      )
      error_message = "The deployment digest must be active in the reviewed releases/approved-workloads.json policy."
    }
  }

  depends_on = [
    google_compute_router_nat.workload,
    google_artifact_registry_repository_iam_member.artifact_reader,
    google_project_iam_member.confidential_workload_user,
    google_project_iam_member.launcher_log_writer,
    google_secret_manager_secret_iam_member.workload_reader,
  ]
}

resource "google_compute_firewall" "load_balancer_to_workload" {
  name      = "${var.name}-allow-google-lb"
  network   = google_compute_network.workload.name
  direction = "INGRESS"
  priority  = 1000

  source_ranges = [
    "35.191.0.0/16",
    "130.211.0.0/22",
  ]
  target_tags = ["simpleunmark-confidential"]

  allow {
    protocol = "tcp"
    ports    = ["8080"]
  }
}

resource "google_compute_instance_group" "workload" {
  name      = "${var.name}-group"
  zone      = var.zone
  instances = [google_compute_instance.workload.self_link]

  named_port {
    name = "http"
    port = 8080
  }
}

resource "google_compute_health_check" "workload" {
  name                = "${var.name}-health"
  check_interval_sec  = 10
  timeout_sec         = 5
  healthy_threshold   = 2
  unhealthy_threshold = 3

  http_health_check {
    port         = 8080
    request_path = "/healthz"
  }
}

resource "google_compute_backend_service" "workload" {
  # v1 intentionally has one backend. Attestation and its request-scoped HPKE
  # key must reach the same process; add session affinity or shared
  # attestation state before introducing additional workload instances.
  name                  = "${var.name}-backend"
  protocol              = "HTTP"
  port_name             = "http"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  # Includes a large browser upload, one bounded FFmpeg pass, and response upload.
  timeout_sec                     = 420
  connection_draining_timeout_sec = 10
  health_checks                   = [google_compute_health_check.workload.id]

  backend {
    group           = google_compute_instance_group.workload.self_link
    balancing_mode  = "UTILIZATION"
    capacity_scaler = 1
  }
}

resource "google_compute_global_address" "workload" {
  name = "${var.name}-ip"
}

resource "google_compute_managed_ssl_certificate" "workload" {
  name = "${var.name}-certificate"

  managed {
    domains = [var.domain_name]
  }
}

resource "google_compute_url_map" "https" {
  name            = "${var.name}-https"
  default_service = google_compute_backend_service.workload.id
}

resource "google_compute_target_https_proxy" "workload" {
  name             = "${var.name}-https-proxy"
  url_map          = google_compute_url_map.https.id
  ssl_certificates = [google_compute_managed_ssl_certificate.workload.id]
}

resource "google_compute_global_forwarding_rule" "https" {
  name                  = "${var.name}-https"
  ip_address            = google_compute_global_address.workload.id
  port_range            = "443"
  target                = google_compute_target_https_proxy.workload.id
  load_balancing_scheme = "EXTERNAL_MANAGED"
}

resource "google_compute_url_map" "http_redirect" {
  name = "${var.name}-http-redirect"

  default_url_redirect {
    https_redirect = true
    strip_query    = false
  }
}

resource "google_compute_target_http_proxy" "redirect" {
  name    = "${var.name}-http-proxy"
  url_map = google_compute_url_map.http_redirect.id
}

resource "google_compute_global_forwarding_rule" "http" {
  name                  = "${var.name}-http"
  ip_address            = google_compute_global_address.workload.id
  port_range            = "80"
  target                = google_compute_target_http_proxy.redirect.id
  load_balancing_scheme = "EXTERNAL_MANAGED"
}
