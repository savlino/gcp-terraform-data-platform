# Private network and temporary IAP demo

Creates a custom VPC (`routing_mode = "REGIONAL"`), one regional IPv4 subnet
with Private Google Access, explicit deny-all ingress `<env>-deny-all-ingress`
(priority 65534, logged without metadata), and IAP-only TCP 22 ingress
`<env>-allow-iap-ssh` (priority 1000) from `35.235.240.0/20` to the `iap-ssh`
network tag. The explicit deny duplicates the implied, unloggable 65535 deny so
the posture is visible in code and denied connections appear in Cloud Logging.
There is no internal allow rule. Lower numeric priority takes precedence.
The deny source `0.0.0.0/0` is a deny, never a public SSH allow.

## Inputs

| Input | Meaning / default |
| --- | --- |
| `project_id`, `env` | Environment project and resource naming/labels |
| `region` | Subnet region; never hardcoded in this module |
| `subnet_cidr` | Required canonical RFC1918 IPv4 CIDR, /8 through /29, fully inside a private range |
| `demo_vm_zone` | Required zone in `region`; root defaults to `${region}-a` |
| `enable_iap_demo_vm` | `false`; creates a billable VM only when explicitly enabled |

The root resolves CIDR defaults to DEV `10.10.0.0/24`, TEST `10.20.0.0/24`
and PROD `10.30.0.0/24`. An explicit `subnet_cidr` overrides the default.
Only DEV environment files are supplied; this step does not deploy TEST/PROD.

## Optional VM and access prerequisites

The demo uses `e2-micro`, a 10 GiB `pd-standard` Debian 12 boot disk, and the
selected region's `a` zone by default (`europe-southwest1-a` for DEV).
It has no external IPv4/IPv6 access configuration, no attached service account,
and no data-plane access grants. Shielded settings enable Secure Boot, vTPM and
integrity monitoring. OS Login is enabled, project SSH keys are blocked and
serial port access is disabled. VM and auto-deleting boot disk have all standard
labels; network/subnet/firewall resources do not support labels in Google 8.7.0.

This module manages no human IAM. The locally applied
[bootstrap](../../bootstrap/README.md#gitlab-federation-and-human-access) grants
an optional human principal project-level IAP access in DEV/TEST only, independent
of the VM flag. The firewall still permits only IAP TCP 22 to tagged VMs.
OS Login authorization is a separate existing/manual prerequisite; this
repository no longer creates a human OS Login grant. An Owner's effective access
does not prove the explicit tunnel grant works in isolation.
Discovery permissions needed by gcloud (`compute.projects.get` and instance
lookup/listing) are also operator prerequisites.
See [OS Login roles](https://docs.cloud.google.com/compute/docs/oslogin/set-up-oslogin)
and [IAP TCP forwarding](https://docs.cloud.google.com/iap/docs/using-tcp-forwarding).

## Boundaries and costs

Cloud Run is **not** attached to this VPC. There is no connector, NAT, bastion,
load balancer or VPN. Private Google Access permits supported Google API access,
not general internet egress; there is no package-update internet path without
additional infrastructure. The normal implied egress allow and system routes
are not an egress restriction policy. Higher-level firewall policies can still
affect effective behavior.

`e2-micro` in European regions is **not** covered by the Compute Engine free-tier
VM allowance. CPU/RAM and disk incur charges while the demo exists; stopping
alone retains billable disk. Enable only for a short supervised test, then set
the flag false and apply immediately. The boot disk has `auto_delete = true`.
This module does not enforce a timer or automatic shutdown.

Outputs: `network_name`, `subnet_name`, `subnet_cidr`, and nullable
`iap_demo_vm_name` / `iap_demo_vm_zone`.

## Deployment and verification

Use the [private-network runbook](../../docs/runbook.md#private-network-and-iap-demo)
for manual removal of the GCP default network, reviewed plan/apply, positive SSH,
negative firewall verification and immediate teardown. This module does not
import/delete the auto-created `default` network.

Plan-only tests with a mock Google provider cover CIDRs, regional routing,
rule names/logging, the invariant that no rule allows `0.0.0.0/0`, default-off
compute, Shielded/private VM settings and invalid inputs:
[network.tftest.hcl](../../infra/tests/network.tftest.hcl).
No live networking or SSH behavior is claimed from those tests.
