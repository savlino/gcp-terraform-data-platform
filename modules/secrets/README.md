# Alert Telegram secret

Creates the `alert-telegram` Secret Manager container (payload: JSON
`{"bot_token": ..., "chat_id": ...}`) and one additive secret-scoped
`roles/secretmanager.secretAccessor` grant for the ingestion runtime identity.
No secret versions, payload variables, keys or secret-reading data sources exist.

| Input | Purpose |
| --- | --- |
| `project_id` | Existing environment project |
| `env` | `dev`, `test`, or `prod` label |
| `region` | User-managed replica location |
| `runtime_sa_email` | Runtime account email, wired from the IAM module output |

Outputs: `secret_id` (short ID), `secret_name` (full resource name).
The secret carries project/env/managed_by labels. IAM resources do not support
labels. Separate projects isolate identically named secrets.

Values are added manually; see [the runbook](../../docs/runbook.md#telegram-alert-secret)
and [security notes](../../docs/security.md#alert-telegram-secret).
The optional Terraform-managed version path is not implemented. If needed later,
it must use a sensitive ephemeral input with `secret_data_wo` and an explicitly
incremented `secret_data_wo_version`, never ordinary payload arguments. Write-only
values are omitted from state/plan but require secure provisioning and explicit
rotation counters; Terraform cannot read them back to detect payload drift.
Manual versions avoid passing the bot token through Terraform altogether.
