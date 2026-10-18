module "storage" {
  source = "../modules/storage"

  project_id         = var.project_id
  env                = var.env
  location           = var.region
  force_destroy      = var.force_destroy
  raw_retention_days = var.raw_retention_days
}

module "bigquery" {
  source = "../modules/bigquery"

  project_id                 = var.project_id
  env                        = var.env
  location                   = var.region
  deletion_protection        = var.deletion_protection
  delete_contents_on_destroy = var.delete_contents_on_destroy
}
