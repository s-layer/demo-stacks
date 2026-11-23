# Offline tests for the data root: the AWS provider is mocked, so nothing reaches AWS
# and no credentials are needed. They check what this configuration asks AWS for,
# not how AWS answers. Run with `terraform test` after `terraform init -backend=false`.

mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = {
      account_id = "111122223333"
    }
  }

  # The namespace validates table_bucket_arn as an ARN, so the mock must return one.
  mock_resource "aws_s3tables_table_bucket" {
    defaults = {
      arn = "arn:aws:s3tables:eu-west-2:111122223333:bucket/mock-tpch"
    }
  }
}

run "defaults" {
  command = apply

  assert {
    condition     = aws_s3tables_table_bucket.tpch.name == "s-layer-demo-tpch"
    error_message = "Table bucket should be named <name_prefix>-tpch."
  }

  assert {
    condition = (
      sort([for ns in aws_s3tables_namespace.tpch : ns.namespace]) ==
      tolist(["tpch_sf1", "tpch_sf10", "tpch_sf100"])
    )
    error_message = "Expected one namespace tpch_sf<N> per default scale factor."
  }

  assert {
    condition = alltrue([
      for ns in aws_s3tables_namespace.tpch :
      ns.table_bucket_arn == aws_s3tables_table_bucket.tpch.arn
    ])
    error_message = "Every namespace should live in the stack's table bucket."
  }

  assert {
    condition     = aws_s3_bucket.results.bucket == "s-layer-demo-results-111122223333"
    error_message = "Results bucket should be <name_prefix>-results-<account id>."
  }

  assert {
    condition = (
      aws_s3_bucket_public_access_block.results.bucket == aws_s3_bucket.results.id &&
      aws_s3_bucket_public_access_block.results.block_public_acls &&
      aws_s3_bucket_public_access_block.results.block_public_policy &&
      aws_s3_bucket_public_access_block.results.ignore_public_acls &&
      aws_s3_bucket_public_access_block.results.restrict_public_buckets
    )
    error_message = "All four public access blocks should be on for the results bucket."
  }

  assert {
    condition = (
      aws_s3_bucket_lifecycle_configuration.results.bucket == aws_s3_bucket.results.id &&
      aws_s3_bucket_lifecycle_configuration.results.rule[0].status == "Enabled" &&
      aws_s3_bucket_lifecycle_configuration.results.rule[0].expiration[0].days == 365
    )
    error_message = "Results should expire after results_retention_days (default 365)."
  }

  assert {
    condition     = output.namespaces == tolist(["tpch_sf1", "tpch_sf10", "tpch_sf100"])
    error_message = "The namespaces output should list every namespace, sorted."
  }

  assert {
    condition     = output.table_bucket_arn == aws_s3tables_table_bucket.tpch.arn
    error_message = "table_bucket_arn output should be the table bucket's ARN."
  }

  assert {
    condition     = output.results_bucket == "s-layer-demo-results-111122223333"
    error_message = "results_bucket output should name the results bucket."
  }
}

run "custom_prefix_scale_factors_and_retention" {
  command = apply

  variables {
    name_prefix            = "acme"
    scale_factors          = [3, 30]
    results_retention_days = 30
  }

  assert {
    condition     = aws_s3tables_table_bucket.tpch.name == "acme-tpch"
    error_message = "Table bucket name should follow name_prefix."
  }

  assert {
    condition     = output.namespaces == tolist(["tpch_sf3", "tpch_sf30"])
    error_message = "Namespaces should follow scale_factors."
  }

  assert {
    condition     = aws_s3_bucket.results.bucket == "acme-results-111122223333"
    error_message = "Results bucket name should follow name_prefix."
  }

  assert {
    condition     = aws_s3_bucket_lifecycle_configuration.results.rule[0].expiration[0].days == 30
    error_message = "Expiry should follow results_retention_days."
  }
}

run "rejects_fractional_scale_factor" {
  command = plan

  variables {
    scale_factors = [1, 1.5]
  }

  expect_failures = [var.scale_factors]
}

run "rejects_scale_factor_below_one" {
  command = plan

  variables {
    scale_factors = [0]
  }

  expect_failures = [var.scale_factors]
}
