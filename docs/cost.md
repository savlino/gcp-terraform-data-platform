# Cost notes

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
