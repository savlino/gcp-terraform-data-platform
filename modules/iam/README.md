# Ingestion runtime IAM

Creates `ingest-runtime` in an existing environment project and additive
`*_iam_member` grants only. The fixed account ID follows the declared contract;
its display name is `<env>-ingest-runtime`. Service accounts and IAM member
resources do not support the standard resource labels.

| Input | Purpose | Default |
| --- | --- | --- |
| `project_id` | Existing environment project | Required |
| `env` | `dev`, `test`, or `prod` | Required |
| `raw_bucket_name` | Existing raw bucket, without `gs://` | Required |
| `dataset_id` | Existing dataset in the project | Required |

Output: `service_account_email`.

The runtime receives `roles/storage.objectUser` on the raw bucket,
`roles/bigquery.dataEditor` on the dataset, and `roles/bigquery.jobUser` on the
project. Object deletion is intentional: replacing an existing raw object after
a source ETag change requires both `storage.objects.create` and
`storage.objects.delete`. The role also supplies object reads/listing for
external-table queries. No bucket-administration permission is granted.

Query-job creation is project-level because jobs are project resources, not
dataset resources. It does not grant access to other datasets. No project-level
data editor, basic role or service-account key is created.

This module manages no human access. Project-level DEV/TEST human Token Creator
grants belong to the locally applied [bootstrap](../../bootstrap/README.md#gitlab-federation-and-human-access).
No human PROD grants are configured there. The runtime is not a Terraform deployer.

Do not combine the dataset IAM member with `google_bigquery_dataset_access`
or explicit dataset `access` blocks. Dataset IAM resources also remove authorized
view permissions; authorized views are not part of this platform.

See [security and live verification](../../docs/security.md).
