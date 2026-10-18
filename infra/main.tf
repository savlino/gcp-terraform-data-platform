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

module "iam" {
  source = "../modules/iam"

  project_id          = var.project_id
  env                 = var.env
  raw_bucket_name     = module.storage.raw_bucket_name
  dataset_id          = module.bigquery.dataset_id
  developer_principal = var.developer_principal
}
