output "instance_id" {
  value = aws_instance.runner.id
}

output "results_prefix" {
  description = "Where the runner uploads its log and results."
  value       = "s3://${var.results_bucket}/results/runs/${var.run_id}/"
}
