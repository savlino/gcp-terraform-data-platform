locals {
  labels = {
    project    = "gcp-tf-portfolio"
    env        = var.env
    managed_by = "terraform"
  }
}

resource "google_compute_network" "private" {
  project                 = var.project_id
  name                    = "${var.env}-private"
  description             = "Custom ${var.env} VPC; Cloud Run is not attached."
  auto_create_subnetworks = false
  routing_mode            = "REGIONAL"
}

resource "google_compute_subnetwork" "private" {
  project                  = var.project_id
  name                     = "${var.env}-private"
  description              = "Private IPv4 subnet with Private Google Access."
  region                   = var.region
  network                  = google_compute_network.private.id
  ip_cidr_range            = var.subnet_cidr
  private_ip_google_access = true
  stack_type               = "IPV4_ONLY"
}

# Every VPC already has an implied deny-all ingress rule at priority 65535. It is
# invisible in code and cannot be logged; this explicit copy at 65534 documents the
# default posture in code and, via log_config, makes denied connections visible in
# Cloud Logging. https://cloud.google.com/firewall/docs/firewalls#default_firewall_rules
resource "google_compute_firewall" "deny_ingress" {
  project       = var.project_id
  name          = "${var.env}-deny-all-ingress"
  description   = "Explicit deny-all ingress (implied 65535 rule made visible and logged)."
  network       = google_compute_network.private.id
  direction     = "INGRESS"
  priority      = 65534
  source_ranges = ["0.0.0.0/0"]

  deny {
    protocol = "all"
  }

  log_config {
    metadata = "EXCLUDE_ALL_METADATA"
  }
}

resource "google_compute_firewall" "iap_ssh" {
  project     = var.project_id
  name        = "${var.env}-allow-iap-ssh"
  description = "SSH only from IAP TCP forwarding proxies to iap-ssh tagged VMs."
  network     = google_compute_network.private.id
  direction   = "INGRESS"
  priority    = 1000
  # Official IAP TCP forwarding proxy range: https://docs.cloud.google.com/iap/docs/using-tcp-forwarding
  source_ranges = ["35.235.240.0/20"]
  target_tags   = ["iap-ssh"]

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
}

resource "google_compute_instance" "iap_demo" {
  count = var.enable_iap_demo_vm ? 1 : 0

  project      = var.project_id
  name         = "${var.env}-iap-demo"
  machine_type = "e2-micro"
  zone         = var.demo_vm_zone
  labels       = local.labels
  tags         = ["iap-ssh"]

  boot_disk {
    auto_delete = true
    initialize_params {
      image  = "debian-cloud/debian-12"
      size   = 10
      type   = "pd-standard"
      labels = local.labels
    }
  }

  # No access_config block: its absence is what means "no external IP".
  network_interface {
    subnetwork = google_compute_subnetwork.private.id
  }

  metadata = {
    enable-oslogin         = "TRUE"
    block-project-ssh-keys = "TRUE"
    serial-port-enable     = "FALSE"
  }

  shielded_instance_config {
    enable_secure_boot          = true
    enable_vtpm                 = true
    enable_integrity_monitoring = true
  }

  depends_on = [google_compute_firewall.deny_ingress, google_compute_firewall.iap_ssh]
}
