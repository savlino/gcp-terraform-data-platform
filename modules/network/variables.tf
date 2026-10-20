variable "project_id" {
  type        = string
  description = "Environment project for the private VPC and optional demo."
}

variable "env" {
  type        = string
  description = "Environment name for resource naming and labels."

  validation {
    condition     = contains(["dev", "test", "prod"], var.env)
    error_message = "env must be dev, test or prod."
  }
}

variable "region" {
  type        = string
  description = "Regional location of the private subnet."
}

variable "subnet_cidr" {
  type        = string
  description = "Canonical private RFC1918 IPv4 subnet CIDR."

  validation {
    condition = try(
      cidrnetmask(var.subnet_cidr) != "" &&
      cidrhost(var.subnet_cidr, 0) == split("/", var.subnet_cidr)[0] &&
      tonumber(split("/", var.subnet_cidr)[1]) >= 8 &&
      tonumber(split("/", var.subnet_cidr)[1]) <= 29 &&
      (
        (split(".", cidrhost(var.subnet_cidr, 0))[0] == "10") ||
        (split(".", cidrhost(var.subnet_cidr, 0))[0] == "172" &&
          tonumber(split(".", cidrhost(var.subnet_cidr, 0))[1]) >= 16 &&
          tonumber(split(".", cidrhost(var.subnet_cidr, 0))[1]) <= 31 &&
        tonumber(split("/", var.subnet_cidr)[1]) >= 12) ||
        (startswith(cidrhost(var.subnet_cidr, 0), "192.168.") &&
        tonumber(split("/", var.subnet_cidr)[1]) >= 16)
      ),
      false
    )
    error_message = "subnet_cidr must be a canonical RFC1918 IPv4 network with prefix /8 through /29 wholly inside its private range."
  }
}

variable "enable_iap_demo_vm" {
  type        = bool
  description = "Create the billable demo VM only for temporary verification; destroy immediately afterward."
  default     = false
}

variable "demo_vm_zone" {
  type        = string
  description = "Demo VM zone in region; the root defaults to the region's a zone."

  validation {
    condition     = can(regex("^[a-z]+-[a-z]+[0-9]+-[a-z]$", var.demo_vm_zone)) && startswith(var.demo_vm_zone, "${var.region}-")
    error_message = "demo_vm_zone must be a zone within region."
  }
}
