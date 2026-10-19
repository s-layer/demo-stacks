# The long-lived half of the stack: storage only, nothing that bills while idle
# beyond bytes stored. Compute lives in ../runner and is created per run.

data "aws_caller_identity" "current" {}

# Iceberg tables for the TPC-H dataset. The tables themselves are created by
# `demo-stacks load` through DuckDB's iceberg extension, not by Terraform, so a
# reload never needs a Terraform change.
resource "aws_s3tables_table_bucket" "tpch" {
  name = "${var.name_prefix}-tpch"
}

resource "aws_s3tables_namespace" "tpch" {
  for_each         = toset([for sf in var.scale_factors : tostring(sf)])
  namespace        = "tpch_sf${each.key}"
  table_bucket_arn = aws_s3tables_table_bucket.tpch.arn
}

# Runner logs and benchmark results.
resource "aws_s3_bucket" "results" {
  bucket = "${var.name_prefix}-results-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket_public_access_block" "results" {
  bucket                  = aws_s3_bucket.results.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "results" {
  bucket = aws_s3_bucket.results.id
  rule {
    id     = "expire-results"
    status = "Enabled"
    filter {}
    expiration {
      days = var.results_retention_days
    }
  }
}
