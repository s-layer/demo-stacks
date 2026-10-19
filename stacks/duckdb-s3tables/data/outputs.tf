output "region" {
  value = var.region
}

output "table_bucket_arn" {
  description = "Pass to the runner as table_bucket_arn, and to `demo-stacks load --s3-tables-arn`."
  value       = aws_s3tables_table_bucket.tpch.arn
}

output "namespaces" {
  value = sort([for ns in aws_s3tables_namespace.tpch : ns.namespace])
}

output "results_bucket" {
  description = "Pass to the runner as results_bucket."
  value       = aws_s3_bucket.results.bucket
}
