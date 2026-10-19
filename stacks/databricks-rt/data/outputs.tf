output "catalog" {
  value = databricks_catalog.demo.name
}

output "schemas" {
  value = sort([for s in databricks_schema.tpch : s.name])
}

output "staging_uri" {
  description = "Where `just dbx-stage` uploads Parquet; one prefix per scale factor below it."
  value       = "s3://${aws_s3_bucket.storage.bucket}/staging"
}

output "loader_http_path" {
  description = "HTTP path of the serverless loader warehouse, for `demo-stacks dbx-load`."
  value       = databricks_sql_endpoint.loader.odbc_params[0].path
}
