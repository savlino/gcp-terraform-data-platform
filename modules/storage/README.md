# Raw storage module

Creates one bucket, `<project_id>-raw`, with uniform bucket-level access,
enforced public access prevention, and the standard project/env/managed_by labels.
No IAM grants are created.

| Input | Purpose | Default |
| --- | --- | --- |
| `project_id` | Existing environment project | Required |
| `env` | `dev`, `test` or `prod` | Required |
| `location` | Same regional location as BigQuery | Required |
| `force_destroy` | Allow deletion of a non-empty bucket | `false` |
| `raw_retention_days` | Optional positive integer object age for lifecycle deletion; `null` disables the rule | `null` |

Output: `raw_bucket_name`.

The lifecycle Delete rule is controlled by `raw_retention_days`: set a positive
value to enable it, or `null` to disable it.
Soft delete is explicitly disabled because raw files are reproducible and retained
soft-deleted objects incur storage costs. This also means deleted raw files cannot
be restored from this bucket. No versioning or retention lock is configured.
Lifecycle deletion is asynchronous, not an exact deletion deadline.

The app will store source objects beneath
`ookla/type=<fixed|mobile>/year=<YYYY>/quarter=<1-4>/`.
No objects or real source data are uploaded by this module.
