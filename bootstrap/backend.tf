terraform {
  backend "gcs" {
    bucket = "dep-2610-ops-tf-state"
    prefix = "bootstrap"
  }
}
