# Repositories cannot move projects. Retain existing images for manual copy and
# rollback; only the new shared ops repository is managed by bootstrap.
removed {
  from = module.ingest_job.google_artifact_registry_repository.ingest

  lifecycle {
    destroy = false
  }
}
