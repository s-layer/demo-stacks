variable "region" {
  description = "Must match the data stack's region."
  type        = string
  default     = "eu-west-2"
}

variable "table_bucket_arn" {
  description = "From the data stack: terraform -chdir=../data output -raw table_bucket_arn"
  type        = string
}

variable "results_bucket" {
  description = "From the data stack: terraform -chdir=../data output -raw results_bucket"
  type        = string
}

variable "run_id" {
  description = "Names this run. Logs land under s3://<results_bucket>/runs/<run_id>/."
  type        = string
  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{0,62}$", var.run_id))
    error_message = "run_id must be lowercase letters, digits and dashes."
  }
}

variable "job" {
  description = "What the runner does: \"load\" generates TPC-H at scale_factor and loads it into the table bucket, \"shell\" runs `command`."
  type        = string
  default     = "load"
  validation {
    condition     = contains(["load", "shell"], var.job)
    error_message = "job must be \"load\" or \"shell\"."
  }
}

variable "command" {
  description = "For job = \"shell\": a command run in the repo checkout with TABLE_BUCKET_ARN, AWS_REGION and RESULTS_DIR set. Files written to RESULTS_DIR are uploaded."
  type        = string
  default     = ""
}

variable "scale_factor" {
  description = "TPC-H scale factor for job = \"load\"; loads into namespace tpch_sf<N>, which the data stack must declare. SF10 is ~60M lineitem rows, SF100 ~600M."
  type        = number
  default     = 10
  validation {
    condition     = var.scale_factor >= 1 && floor(var.scale_factor) == var.scale_factor
    error_message = "scale_factor must be a whole number of at least 1."
  }
}

variable "chunks" {
  description = "Slices to generate TPC-H in; about one per 10 SF keeps memory bounded."
  type        = number
  default     = 1
}

variable "instance_type" {
  description = "Runner instance size. r7i.2xlarge (64 GB) handles SF100 generation in 10 chunks."
  type        = string
  default     = "r7i.2xlarge"
}

variable "volume_size_gb" {
  description = "Root volume for the repo and generated Parquet (SF100 needs ~40 GB)."
  type        = number
  default     = 100
}

variable "max_lifetime_minutes" {
  description = "Hard stop: the instance powers off (and so terminates) after this long, whatever the job is doing."
  type        = number
  default     = 240
}

variable "repo_url" {
  type    = string
  default = "https://github.com/s-layer/demo-stacks.git"
}

variable "repo_ref" {
  description = "Branch, tag or commit of demo-stacks to run."
  type        = string
  default     = "main"
}
