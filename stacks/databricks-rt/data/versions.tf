terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    databricks = {
      source  = "databricks/databricks"
      version = "~> 1.90"
    }
    time = {
      source  = "hashicorp/time"
      version = "~> 0.13"
    }
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = {
      project = "s-layer-demo-stacks"
      stack   = "databricks-rt"
      layer   = "data"
    }
  }
}

# Workspace-level provider. Authenticate with DATABRICKS_HOST plus DATABRICKS_TOKEN
# (or a profile in ~/.databrickscfg via DATABRICKS_CONFIG_PROFILE).
provider "databricks" {}
