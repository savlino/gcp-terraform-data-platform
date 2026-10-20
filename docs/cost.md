# Cost notes

## Temporary IAP VM

The optional `e2-micro` demo in `europe-southwest1-a` is not eligible for the
Compute Engine free-tier VM allowance, which is limited to selected US regions.
The flag `enable_iap_demo_vm` defaults false in root/module and DEV tfvars.
Enable it only for a supervised SSH test, then set false and apply immediately.
CPU/RAM and the 10 GiB `pd-standard` boot disk are billable; stopping the VM
does not delete the disk. Terraform destroys the auto-deleting disk when it
destroys this VM. There is no automatic TTL or shutdown.

Check current [Compute Engine pricing](https://cloud.google.com/compute/all-pricing)
and [free-tier restrictions](https://cloud.google.com/free/docs/free-cloud-features#compute)
before enabling. No measured VM bill or fixed EU hourly-price claim is made.
No bastion, Cloud NAT, VPC connector, VPN or external IP is provisioned.

## Firewall rule logging

Logging is enabled only on the explicit deny-all ingress rule, without metadata.
Records are generated per denied connection reaching a VM in this VPC. Without
VMs nothing is logged; the demo VM has no external IP, so internet scans cannot
reach it, and IAP SSH matches the higher-priority allow rule. Expected volume is
near zero, far below the Cloud Logging monthly free ingestion allotment (check
[Cloud Logging pricing](https://cloud.google.com/stackdriver/pricing)).
No measured log volume is claimed.

## Artifact Registry

Conservative storage estimate for Artifact Registry image retention:

- Tagged image size: ~300-500 MiB per build (`ingest` image with Python + Arrow + Google SDKs).
- Expected total builds for this portfolio: 10-20.
- Tagged images are immutable and intentionally retained.
- Cleanup policy deletes only **untagged** versions older than 7 days (failed/partial pushes).

Estimated retained tagged storage:

- Low case: `10 * 0.3 GiB = 3 GiB`
- High case: `20 * 0.5 GiB = 10 GiB`

Using a typical Artifact Registry storage price around `$0.10/GiB-month` (check the official pricing page for your billing account and region):

- Low case: `3 GiB * $0.10 = $0.30/month`
- High case: `10 GiB * $0.10 = $1.00/month`

So for this project scale, registry storage is expected to stay in the
single-digit GB range, with monthly cost roughly in the **tens of cents to about one dollar**.
