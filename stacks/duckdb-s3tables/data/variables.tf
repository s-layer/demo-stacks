variable "region" {
  description = "AWS region for the table bucket and results bucket."
  type        = string
  default     = "eu-west-2"
}

variable "name_prefix" {
  description = "Prefix for bucket names. Table bucket and S3 bucket names are global, so make it unique to your account."
  type        = string
  default     = "s-layer-demo"
}

variable "scale_factors" {
  description = "TPC-H scale factors to keep. Each gets an Iceberg namespace tpch_sf<N> (see demo_stacks.s3tables.namespace); the runner's load job fills it."
  type        = list(number)
  default     = [1, 10, 100]
  validation {
    condition     = alltrue([for sf in var.scale_factors : sf >= 1 && floor(sf) == sf])
    error_message = "scale_factors must be whole numbers of at least 1."
  }
}

variable "results_retention_days" {
  description = "Days to keep runner logs and benchmark results before S3 expires them."
  type        = number
  default     = 365
}
