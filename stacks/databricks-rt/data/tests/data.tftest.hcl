# Offline tests for the Databricks data root: the AWS, Databricks and time providers
# are mocked, so nothing reaches AWS or a workspace and no credentials are needed.
# They check that the bucket, IAM role and Unity Catalog objects this configuration
# asks for agree with each other, not how AWS or Databricks answer.
# Run with `terraform test` after `terraform init -backend=false`.

mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = {
      account_id = "111122223333"
    }
  }
}

mock_provider "databricks" {
  # The external ID Databricks assigns to the storage credential, which the role's
  # trust policy must name.
  mock_resource "databricks_storage_credential" {
    defaults = {
      aws_iam_role = {
        external_id = "mock-external-id"
      }
    }
  }

  mock_resource "databricks_sql_endpoint" {
    defaults = {
      odbc_params = [{
        hostname = "mock.cloud.databricks.com"
        path     = "/sql/1.0/warehouses/mock-loader"
        port     = 443
        protocol = "https"
      }]
    }
  }
}

mock_provider "time" {}

# A mocked data source does not render the policy, so each document gets a distinct
# stand-in JSON that passes IAM's JSON validation. The tests read the arguments each
# document was given, and use the stand-ins to check which document goes where.
override_data {
  target = data.databricks_aws_unity_catalog_assume_role_policy.storage
  values = {
    json = "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Sid\":\"MockTrust\"}]}"
  }
}

override_data {
  target = data.databricks_aws_unity_catalog_policy.storage
  values = {
    json = "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Sid\":\"MockAccess\"}]}"
  }
}

run "defaults" {
  command = apply

  assert {
    condition     = aws_s3_bucket.storage.bucket == "s-layer-demo-databricks-111122223333"
    error_message = "Storage bucket should be <name_prefix>-databricks-<account id>."
  }

  assert {
    condition = (
      aws_s3_bucket_public_access_block.storage.bucket == aws_s3_bucket.storage.id &&
      aws_s3_bucket_public_access_block.storage.block_public_acls &&
      aws_s3_bucket_public_access_block.storage.block_public_policy &&
      aws_s3_bucket_public_access_block.storage.ignore_public_acls &&
      aws_s3_bucket_public_access_block.storage.restrict_public_buckets
    )
    error_message = "All four public access blocks should be on for the storage bucket."
  }

  # Only staging/ expires; managed/ holds the catalog's tables.
  assert {
    condition = (
      aws_s3_bucket_lifecycle_configuration.storage.bucket == aws_s3_bucket.storage.id &&
      length(aws_s3_bucket_lifecycle_configuration.storage.rule) == 1 &&
      aws_s3_bucket_lifecycle_configuration.storage.rule[0].status == "Enabled" &&
      aws_s3_bucket_lifecycle_configuration.storage.rule[0].filter[0].prefix == "staging/" &&
      aws_s3_bucket_lifecycle_configuration.storage.rule[0].expiration[0].days == 7
    )
    error_message = "Only staging/ should expire, after staging_retention_days (default 7)."
  }

  # The credential names the role by ARN before the role exists, so the two names
  # must agree, or the external location cannot validate the pair.
  assert {
    condition = (
      databricks_storage_credential.storage.aws_iam_role[0].role_arn ==
      "arn:aws:iam::111122223333:role/${aws_iam_role.storage.name}"
    )
    error_message = "The storage credential should name the role this stack creates."
  }

  assert {
    condition     = aws_iam_role.storage.name == "s-layer-demo-uc-storage"
    error_message = "The role should be named <name_prefix>-uc-storage."
  }

  assert {
    condition = (
      data.databricks_aws_unity_catalog_assume_role_policy.storage.aws_account_id == "111122223333" &&
      data.databricks_aws_unity_catalog_assume_role_policy.storage.role_name == aws_iam_role.storage.name &&
      data.databricks_aws_unity_catalog_assume_role_policy.storage.external_id == "mock-external-id"
    )
    error_message = "The trust policy should name this account, the role and the credential's external ID."
  }

  assert {
    condition = (
      data.databricks_aws_unity_catalog_policy.storage.aws_account_id == "111122223333" &&
      data.databricks_aws_unity_catalog_policy.storage.bucket_name == aws_s3_bucket.storage.bucket &&
      data.databricks_aws_unity_catalog_policy.storage.role_name == aws_iam_role.storage.name
    )
    error_message = "The access policy should cover this stack's bucket and role."
  }

  assert {
    condition = (
      aws_iam_role.storage.assume_role_policy == data.databricks_aws_unity_catalog_assume_role_policy.storage.json &&
      aws_iam_role_policy.storage.role == aws_iam_role.storage.id &&
      aws_iam_role_policy.storage.policy == data.databricks_aws_unity_catalog_policy.storage.json
    )
    error_message = "The role should trust the Unity Catalog trust policy and carry the access policy."
  }

  assert {
    condition = (
      databricks_external_location.storage.url == "s3://s-layer-demo-databricks-111122223333/" &&
      databricks_external_location.storage.credential_name == databricks_storage_credential.storage.name
    )
    error_message = "The external location should cover the whole bucket through the storage credential."
  }

  assert {
    condition = (
      databricks_catalog.demo.name == "s_layer_demo" &&
      databricks_catalog.demo.storage_root == "s3://s-layer-demo-databricks-111122223333/managed"
    )
    error_message = "The catalog's managed location should be managed/ in the stack's bucket."
  }

  assert {
    condition = (
      sort([for s in databricks_schema.tpch : s.name]) == tolist(["tpch_sf1", "tpch_sf10"]) &&
      alltrue([for s in databricks_schema.tpch : s.catalog_name == databricks_catalog.demo.name])
    )
    error_message = "Expected one schema tpch_sf<N> per default scale factor, in the demo catalog."
  }

  assert {
    condition = (
      databricks_sql_endpoint.loader.enable_serverless_compute &&
      databricks_sql_endpoint.loader.warehouse_type == "PRO" &&
      databricks_sql_endpoint.loader.cluster_size == "2X-Small" &&
      databricks_sql_endpoint.loader.max_num_clusters == 1 &&
      databricks_sql_endpoint.loader.auto_stop_mins == 5
    )
    error_message = "The loader should be one small serverless warehouse that stops after 5 idle minutes."
  }

  assert {
    condition = (
      output.catalog == "s_layer_demo" &&
      output.schemas == tolist(["tpch_sf1", "tpch_sf10"]) &&
      output.staging_uri == "s3://s-layer-demo-databricks-111122223333/staging" &&
      output.loader_http_path == "/sql/1.0/warehouses/mock-loader"
    )
    error_message = "Outputs should name the catalog, schemas, staging prefix and loader path."
  }
}

run "custom_names_and_retention" {
  command = apply

  variables {
    name_prefix              = "acme"
    catalog                  = "acme_demo"
    scale_factors            = [1, 100]
    staging_retention_days   = 2
    loader_auto_stop_minutes = 10
  }

  assert {
    condition = (
      aws_s3_bucket.storage.bucket == "acme-databricks-111122223333" &&
      aws_iam_role.storage.name == "acme-uc-storage" &&
      databricks_storage_credential.storage.name == "acme-storage" &&
      databricks_external_location.storage.name == "acme-storage" &&
      databricks_sql_endpoint.loader.name == "acme-loader"
    )
    error_message = "Bucket, role, credential, location and loader names should follow name_prefix."
  }

  assert {
    condition = (
      databricks_catalog.demo.name == "acme_demo" &&
      databricks_catalog.demo.storage_root == "s3://acme-databricks-111122223333/managed" &&
      output.schemas == tolist(["tpch_sf1", "tpch_sf100"])
    )
    error_message = "Catalog and schemas should follow catalog and scale_factors."
  }

  assert {
    condition = (
      aws_s3_bucket_lifecycle_configuration.storage.rule[0].expiration[0].days == 2 &&
      databricks_sql_endpoint.loader.auto_stop_mins == 10
    )
    error_message = "Staging expiry and loader auto stop should follow their variables."
  }
}

run "rejects_fractional_scale_factor" {
  command = plan

  variables {
    scale_factors = [10, 1.5]
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
