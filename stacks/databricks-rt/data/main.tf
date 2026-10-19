# The long-lived half of the stack: one S3 bucket and the Unity Catalog objects over
# it, plus a serverless loader warehouse that stops when idle. Nothing here bills
# while idle beyond bytes stored.
#
# Lakehouse//RT reads only Unity Catalog managed tables, and not ones in the
# workspace's default storage, so the catalog gets its own managed location in this
# bucket. RT is read-only, so loading and DDL run on the loader warehouse instead.
# The RT warehouse itself is not created here: the warehouse type is in beta, must
# be enabled by the Databricks account team, and has no documented API or Terraform
# support yet. Create it in the UI and pass its HTTP path to the query commands.

data "aws_caller_identity" "current" {}

locals {
  bucket    = "${var.name_prefix}-databricks-${data.aws_caller_identity.current.account_id}"
  role_name = "${var.name_prefix}-uc-storage"
}

# managed/ holds the catalog's tables; staging/ holds Parquet from `demo-stacks generate`
# on its way into them.
resource "aws_s3_bucket" "storage" {
  bucket = local.bucket
  # `just dbx-data-destroy` is the explicit "delete everything" path; the catalog
  # goes with it, so its files may go too.
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "storage" {
  bucket                  = aws_s3_bucket.storage.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "storage" {
  bucket = aws_s3_bucket.storage.id
  rule {
    id     = "expire-staging"
    status = "Enabled"
    filter {
      prefix = "staging/"
    }
    expiration {
      days = var.staging_retention_days
    }
  }
}

# The IAM role Unity Catalog assumes to reach the bucket. The policy documents come
# from the Databricks provider so the trust relationship (including the role
# trusting itself, which UC requires) matches what Databricks expects.
data "databricks_aws_unity_catalog_assume_role_policy" "storage" {
  aws_account_id = data.aws_caller_identity.current.account_id
  role_name      = local.role_name
  external_id    = var.databricks_account_id
}

data "databricks_aws_unity_catalog_policy" "storage" {
  aws_account_id = data.aws_caller_identity.current.account_id
  bucket_name    = aws_s3_bucket.storage.bucket
  role_name      = local.role_name
}

resource "aws_iam_role" "storage" {
  name               = local.role_name
  assume_role_policy = data.databricks_aws_unity_catalog_assume_role_policy.storage.json
}

resource "aws_iam_role_policy" "storage" {
  role   = aws_iam_role.storage.id
  policy = data.databricks_aws_unity_catalog_policy.storage.json
}

# IAM changes take a little while to be visible to Unity Catalog's validation.
resource "time_sleep" "iam" {
  depends_on      = [aws_iam_role_policy.storage]
  create_duration = "20s"
}

resource "databricks_storage_credential" "storage" {
  name = "${var.name_prefix}-storage"
  aws_iam_role {
    role_arn = aws_iam_role.storage.arn
  }
  depends_on = [time_sleep.iam]
}

resource "databricks_external_location" "storage" {
  name            = "${var.name_prefix}-storage"
  url             = "s3://${aws_s3_bucket.storage.bucket}/"
  credential_name = databricks_storage_credential.storage.name
}

resource "databricks_catalog" "demo" {
  name          = var.catalog
  storage_root  = "s3://${aws_s3_bucket.storage.bucket}/managed"
  comment       = "s-layer demo stacks: TPC-H tables and metric views."
  force_destroy = true
  depends_on    = [databricks_external_location.storage]
}

resource "databricks_schema" "tpch" {
  for_each      = toset([for sf in var.scale_factors : tostring(sf)])
  catalog_name  = databricks_catalog.demo.name
  name          = "tpch_sf${each.key}"
  force_destroy = true
}

# Runs the loads (CTAS from staging) and the metric view DDL, which RT cannot.
resource "databricks_sql_endpoint" "loader" {
  name                      = "${var.name_prefix}-loader"
  cluster_size              = "2X-Small"
  max_num_clusters          = 1
  auto_stop_mins            = var.loader_auto_stop_minutes
  enable_serverless_compute = true
  warehouse_type            = "PRO"
}
