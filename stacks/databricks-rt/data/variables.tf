variable "region" {
  description = "AWS region of the Databricks workspace. The storage bucket must be in the same region."
  type        = string
  default     = "eu-west-2"
}

variable "databricks_account_id" {
  description = "Databricks account ID. Unity Catalog uses it as the external ID when it assumes the storage role."
  type        = string
}

variable "name_prefix" {
  description = "Prefix for the bucket, IAM role and Unity Catalog objects. The bucket name is global, so make it unique to your account."
  type        = string
  default     = "s-layer-demo"
}

variable "catalog" {
  description = "Unity Catalog catalog holding the TPC-H schemas and metric views."
  type        = string
  default     = "s_layer_demo"
}

variable "scale_factors" {
  description = "TPC-H scale factors to keep. Each gets a schema tpch_sf<N> (see demo_stacks.s3tables.namespace, which this stack shares)."
  type        = list(number)
  default     = [1, 10]
  validation {
    condition     = alltrue([for sf in var.scale_factors : sf >= 1 && floor(sf) == sf])
    error_message = "scale_factors must be whole numbers of at least 1."
  }
}

variable "loader_auto_stop_minutes" {
  description = "Idle minutes before the loader warehouse stops. It only runs while loading or creating views."
  type        = number
  default     = 5
}

variable "staging_retention_days" {
  description = "Days to keep staged Parquet before S3 expires it. The managed tables are copies, so staging is disposable."
  type        = number
  default     = 7
}
