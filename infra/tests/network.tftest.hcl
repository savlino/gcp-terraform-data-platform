mock_provider "google" {
  mock_resource "google_compute_network" {
    override_during = plan
    defaults = {
      id = "projects/dep-2610-dev/global/networks/dev-private"
    }
  }
  mock_resource "google_compute_subnetwork" {
    override_during = plan
    defaults = {
      id = "projects/dep-2610-dev/regions/europe-southwest1/subnetworks/dev-private"
    }
  }
}

variables {
  project_id   = "dep-2610-dev"
  env          = "dev"
  region       = "europe-southwest1"
  subnet_cidr  = "10.10.0.0/24"
  demo_vm_zone = "europe-southwest1-a"
}

run "private_network_without_vm" {
  command = plan
  module {
    source = "../modules/network"
  }

  assert {
    condition = (
      !google_compute_network.private.auto_create_subnetworks &&
      google_compute_network.private.routing_mode == "REGIONAL" &&
      google_compute_network.private.name == "dev-private" &&
      google_compute_subnetwork.private.region == var.region &&
      google_compute_subnetwork.private.network == google_compute_network.private.id &&
      google_compute_subnetwork.private.ip_cidr_range == var.subnet_cidr &&
      google_compute_subnetwork.private.private_ip_google_access &&
      google_compute_subnetwork.private.stack_type == "IPV4_ONLY"
    )
    error_message = "Use a custom VPC and private IPv4 subnet with Private Google Access."
  }

  assert {
    condition = (
      google_compute_firewall.deny_ingress.name == "dev-deny-all-ingress" &&
      google_compute_firewall.deny_ingress.network == google_compute_network.private.id &&
      google_compute_firewall.deny_ingress.direction == "INGRESS" &&
      google_compute_firewall.deny_ingress.priority == 65534 &&
      google_compute_firewall.deny_ingress.source_ranges == toset(["0.0.0.0/0"]) &&
      google_compute_firewall.deny_ingress.target_tags == null &&
      google_compute_firewall.deny_ingress.target_service_accounts == null &&
      one(google_compute_firewall.deny_ingress.deny).protocol == "all" &&
      length(google_compute_firewall.deny_ingress.allow) == 0 &&
      one(google_compute_firewall.deny_ingress.log_config).metadata == "EXCLUDE_ALL_METADATA" &&
      google_compute_firewall.iap_ssh.name == "dev-allow-iap-ssh" &&
      google_compute_firewall.iap_ssh.network == google_compute_network.private.id &&
      google_compute_firewall.iap_ssh.direction == "INGRESS" &&
      google_compute_firewall.iap_ssh.priority < google_compute_firewall.deny_ingress.priority &&
      google_compute_firewall.iap_ssh.source_ranges == toset(["35.235.240.0/20"]) &&
      google_compute_firewall.iap_ssh.target_tags == toset(["iap-ssh"]) &&
      one(google_compute_firewall.iap_ssh.allow).protocol == "tcp" &&
      one(google_compute_firewall.iap_ssh.allow).ports == tolist(["22"]) &&
      length(google_compute_firewall.iap_ssh.deny) == 0
    )
    error_message = "Deny ingress by default; allow only tagged SSH from the official IAP IPv4 range."
  }

  assert {
    condition = alltrue([
      for rule in [google_compute_firewall.deny_ingress, google_compute_firewall.iap_ssh] :
      !(contains(rule.source_ranges, "0.0.0.0/0") && length(rule.allow) > 0)
    ])
    error_message = "No firewall rule may allow traffic from 0.0.0.0/0."
  }

  assert {
    condition = (
      length(google_compute_instance.iap_demo) == 0 &&
      output.iap_demo_vm_name == null
    )
    error_message = "No VM or demo IAM must exist by default."
  }
}

run "enabled_shielded_vm" {
  command = plan
  module {
    source = "../modules/network"
  }
  variables {
    enable_iap_demo_vm = true
  }

  assert {
    condition = (
      google_compute_instance.iap_demo[0].machine_type == "e2-micro" &&
      google_compute_instance.iap_demo[0].zone == "europe-southwest1-a" &&
      google_compute_instance.iap_demo[0].tags == toset(["iap-ssh"]) &&
      google_compute_instance.iap_demo[0].network_interface[0].subnetwork == google_compute_subnetwork.private.id &&
      length(google_compute_instance.iap_demo[0].network_interface[0].access_config) == 0 &&
      length(google_compute_instance.iap_demo[0].network_interface[0].ipv6_access_config) == 0 &&
      length(google_compute_instance.iap_demo[0].service_account) == 0 &&
      google_compute_instance.iap_demo[0].metadata["enable-oslogin"] == "TRUE" &&
      google_compute_instance.iap_demo[0].metadata["block-project-ssh-keys"] == "TRUE" &&
      google_compute_instance.iap_demo[0].metadata["serial-port-enable"] == "FALSE" &&
      google_compute_instance.iap_demo[0].shielded_instance_config[0].enable_secure_boot &&
      google_compute_instance.iap_demo[0].shielded_instance_config[0].enable_vtpm &&
      google_compute_instance.iap_demo[0].shielded_instance_config[0].enable_integrity_monitoring
    )
    error_message = "Demo must be a Shielded e2-micro without public access or an attached service account, using OS Login."
  }

  assert {
    condition = (
      google_compute_instance.iap_demo[0].labels == tomap({ project = "gcp-tf-portfolio", env = "dev", managed_by = "terraform" }) &&
      google_compute_instance.iap_demo[0].boot_disk[0].auto_delete &&
      google_compute_instance.iap_demo[0].boot_disk[0].initialize_params[0].size == 10 &&
      google_compute_instance.iap_demo[0].boot_disk[0].initialize_params[0].type == "pd-standard" &&
      google_compute_instance.iap_demo[0].boot_disk[0].initialize_params[0].labels == google_compute_instance.iap_demo[0].labels
    )
    error_message = "Label the VM and its auto-deleting disk."
  }
}

run "reject_public_cidr" {
  command = plan
  module {
    source = "../modules/network"
  }
  variables {
    subnet_cidr = "8.8.8.0/24"
  }
  expect_failures = [var.subnet_cidr]
}

run "reject_host_bits" {
  command = plan
  module {
    source = "../modules/network"
  }
  variables {
    subnet_cidr = "10.10.0.1/24"
  }
  expect_failures = [var.subnet_cidr]
}

run "reject_malformed_cidr" {
  command = plan
  module {
    source = "../modules/network"
  }
  variables {
    subnet_cidr = "invalid"
  }
  expect_failures = [var.subnet_cidr]
}

run "reject_private_range_escape" {
  command = plan
  module {
    source = "../modules/network"
  }
  variables {
    subnet_cidr = "172.0.0.0/8"
  }
  expect_failures = [var.subnet_cidr]
}

run "reject_cross_region_zone" {
  command = plan
  module {
    source = "../modules/network"
  }
  variables {
    demo_vm_zone = "europe-west1-b"
  }
  expect_failures = [var.demo_vm_zone]
}
