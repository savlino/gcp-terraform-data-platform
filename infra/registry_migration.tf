# Repositories cannot move projects. Retain existing images for manual copy and
# rollback; only the new shared ops repository is managed by bootstrap.
removed {
  from = module.ingest_job.google_artifact_registry_repository.ingest

  lifecycle {
    destroy = false
  }
}

# The shared external table was replaced by per-file schema detection in the app.
# It was created with deletion_protection = var.deletion_protection, so in PROD the
# state holds `true` and Terraform refuses to destroy it, even in the same apply that
# removes the resource from configuration. Forgetting it avoids a destroy and a
# separate un-protect apply. Delete the leftover table by hand after apply
# (runbook, "Per-file schema migration"); raw objects in GCS are not affected.
removed {
  from = module.bigquery.google_bigquery_table.ext_ookla_raw

  lifecycle {
    destroy = false
  }
}
