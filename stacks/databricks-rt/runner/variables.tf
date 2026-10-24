variable "region" {
  description = "Must match the data stack's region, which is the workspace's region."
  type        = string
  default     = "eu-west-2"
}

variable "results_bucket" {
  description = "Bucket the runner uploads to, under results/runs/<run_id>/. The data stack's storage bucket: the host of `terraform -chdir=../data output -raw staging_uri`."
  type        = string
}

variable "run_id" {
  description = "Names this run. Logs land under s3://<results_bucket>/results/runs/<run_id>/."
  type        = string
  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{0,62}$", var.run_id))
    error_message = "run_id must be lowercase letters, digits and dashes."
  }
}

variable "command" {
  description = "A command run in the repo checkout with AWS_REGION, DATABRICKS_HOST, DATABRICKS_TOKEN and RESULTS_DIR set. Files written to RESULTS_DIR are uploaded."
  type        = string
}

variable "databricks_host" {
  description = "Workspace URL, as in DATABRICKS_HOST."
  type        = string
}

variable "databricks_token" {
  description = "Personal access token the job connects with. Stored as an SSM SecureString for the runner to fetch, not put in user data; it is still in this root's local state, which `terraform destroy` removes along with the parameter."
  type        = string
  sensitive   = true
}

variable "instance_type" {
  description = "Runner instance size. The job only reads from a warehouse, so a small one will do."
  type        = string
  default     = "m7i.large"
}

variable "volume_size_gb" {
  description = "Root volume for the repo and its Python environment."
  type        = number
  default     = 30
}

variable "max_lifetime_minutes" {
  description = "Hard stop: the instance powers off (and so terminates) after this long, whatever the job is doing."
  type        = number
  default     = 120
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
