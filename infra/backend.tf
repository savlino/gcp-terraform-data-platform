terraform {
  # Separate projects and backend prefixes isolate IAM, billing and state per
  # environment. CLI workspaces share backend access and are not that boundary.
  backend "gcs" {}
}
