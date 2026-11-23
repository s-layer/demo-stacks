# Offline tests for the runner root: the AWS provider is mocked, so nothing reaches AWS
# and no credentials are needed. They check the instance, IAM policy and boot script
# this configuration asks for, not whether AWS or the boot script then behave.
# Run with `terraform test` after `terraform init -backend=false`.

mock_provider "aws" {
  mock_data "aws_ssm_parameter" {
    defaults = {
      value = "ami-0mock"
    }
  }

  mock_data "aws_vpc" {
    defaults = {
      id = "vpc-0mock"
    }
  }

  mock_data "aws_subnets" {
    defaults = {
      ids = ["subnet-0first", "subnet-0second"]
    }
  }
}

# A mocked data source does not render the policy, so each document gets a distinct
# stand-in JSON that passes IAM's JSON validation. The tests read the statements each
# document was given, and use the stand-ins to check which document goes where.
override_data {
  target = data.aws_iam_policy_document.assume
  values = {
    json = "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Sid\":\"MockAssume\"}]}"
  }
}

override_data {
  target = data.aws_iam_policy_document.runner
  values = {
    json = "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Sid\":\"MockRunner\"}]}"
  }
}

variables {
  table_bucket_arn = "arn:aws:s3tables:eu-west-2:111122223333:bucket/mock-tpch"
  results_bucket   = "mock-results"
  run_id           = "run-1"
}

run "load_job_defaults" {
  command = apply

  assert {
    condition     = data.aws_ssm_parameter.al2023.name == "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
    error_message = "The runner should boot the latest Amazon Linux 2023 AMI."
  }

  assert {
    condition = (
      aws_instance.runner.ami == "ami-0mock" &&
      aws_instance.runner.instance_type == "r7i.2xlarge" &&
      aws_instance.runner.subnet_id == "subnet-0first" &&
      aws_instance.runner.associate_public_ip_address
    )
    error_message = "Runner should use the AL2023 AMI, the default type, and a public default subnet."
  }

  assert {
    condition     = aws_instance.runner.instance_initiated_shutdown_behavior == "terminate"
    error_message = "Powering off must terminate the instance, or a finished runner keeps billing."
  }

  assert {
    condition     = aws_instance.runner.metadata_options[0].http_tokens == "required"
    error_message = "The runner should require IMDSv2."
  }

  assert {
    condition = (
      aws_instance.runner.root_block_device[0].volume_size == 100 &&
      aws_instance.runner.root_block_device[0].volume_type == "gp3" &&
      aws_instance.runner.root_block_device[0].delete_on_termination
    )
    error_message = "Root volume should be 100 GB gp3 and deleted with the instance."
  }

  assert {
    condition = (
      aws_security_group.runner.vpc_id == "vpc-0mock" &&
      length(aws_security_group.runner.ingress) == 0 &&
      length(aws_security_group.runner.egress) == 1 &&
      one(aws_security_group.runner.egress).cidr_blocks == tolist(["0.0.0.0/0"])
    )
    error_message = "The security group should allow egress only, in the default VPC."
  }

  assert {
    condition     = aws_instance.runner.vpc_security_group_ids == toset([aws_security_group.runner.id])
    error_message = "The runner should use only its own security group."
  }

  assert {
    condition     = aws_instance.runner.iam_instance_profile == aws_iam_instance_profile.runner.name
    error_message = "The runner should use its own instance profile."
  }

  assert {
    condition     = aws_iam_role.runner.assume_role_policy == data.aws_iam_policy_document.assume.json
    error_message = "The runner role should trust the assume-role document."
  }

  assert {
    condition     = aws_iam_instance_profile.runner.role == aws_iam_role.runner.name
    error_message = "The instance profile should carry the runner role."
  }

  assert {
    condition = (
      aws_iam_role_policy_attachment.ssm.role == aws_iam_role.runner.name &&
      aws_iam_role_policy_attachment.ssm.policy_arn == "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
    )
    error_message = "The runner role should get SSM Session Manager access."
  }

  assert {
    condition = (
      one(data.aws_iam_policy_document.assume.statement).actions == toset(["sts:AssumeRole"]) &&
      one(one(data.aws_iam_policy_document.assume.statement).principals).identifiers == toset(["ec2.amazonaws.com"])
    )
    error_message = "Only EC2 should be able to assume the runner role."
  }

  assert {
    condition     = aws_instance.runner.tags["Name"] == "demo-stacks-runner-run-1"
    error_message = "The instance should be named after the run."
  }

  assert {
    condition     = output.results_prefix == "s3://mock-results/runs/run-1/"
    error_message = "results_prefix should point at this run's prefix."
  }

  # The boot script: hard stop, environment, checkout and the load job.
  assert {
    condition = alltrue([
      for line in [
        "shutdown -h +240",
        "export AWS_REGION='eu-west-2'",
        "export TABLE_BUCKET_ARN='arn:aws:s3tables:eu-west-2:111122223333:bucket/mock-tpch'",
        "RUN_PREFIX='s3://mock-results/runs/run-1'",
        "git clone --quiet 'https://github.com/s-layer/demo-stacks.git' /data/repo",
        "git checkout --quiet 'main'",
        "uv run demo-stacks generate --sf 10 --chunks 1 --out /data/tpch && uv run demo-stacks load --parquet /data/tpch --s3-tables-arn \"$TABLE_BUCKET_ARN\" --region \"$AWS_REGION\" --sf 10 --database \"$RESULTS_DIR/views.duckdb\"",
      ] : contains(split("\n", aws_instance.runner.user_data), line)
    ])
    error_message = "The boot script should set up the run and end with the load job for SF10."
  }

  assert {
    condition     = !strcontains(aws_instance.runner.user_data, "demo-stacks bench")
    error_message = "A load job should not run the benchmark."
  }
}

run "load_job_sf100_in_chunks" {
  command = apply

  variables {
    scale_factor = 100
    chunks       = 10
  }

  assert {
    condition = contains(
      split("\n", aws_instance.runner.user_data),
      "uv run demo-stacks generate --sf 100 --chunks 10 --out /data/tpch && uv run demo-stacks load --parquet /data/tpch --s3-tables-arn \"$TABLE_BUCKET_ARN\" --region \"$AWS_REGION\" --sf 100 --database \"$RESULTS_DIR/views.duckdb\"",
    )
    error_message = "A load job should generate and load the requested scale factor in the requested chunks."
  }
}

run "bench_job" {
  command = apply

  variables {
    job                  = "bench"
    scale_factor         = 100
    bench_repeats        = 20
    instance_type        = "r7i.4xlarge"
    repo_ref             = "abc1234"
    max_lifetime_minutes = 60
  }

  assert {
    condition = alltrue([
      for line in [
        "shutdown -h +60",
        "git checkout --quiet 'abc1234'",
        "uv run demo-stacks load --views-only --s3-tables-arn \"$TABLE_BUCKET_ARN\" --region \"$AWS_REGION\" --sf 100 --database \"$RESULTS_DIR/views.duckdb\" && uv run demo-stacks bench --database \"$RESULTS_DIR/views.duckdb\" --s3-tables-arn \"$TABLE_BUCKET_ARN\" --region \"$AWS_REGION\" --repeats 20 --out \"$RESULTS_DIR/bench.json\"",
      ] : contains(split("\n", aws_instance.runner.user_data), line)
    ])
    error_message = "A bench job should create the views over tpch_sf100, then benchmark them 20 times."
  }

  assert {
    condition     = !strcontains(aws_instance.runner.user_data, "demo-stacks generate")
    error_message = "A bench job should not regenerate data."
  }

  assert {
    condition     = aws_instance.runner.instance_type == "r7i.4xlarge"
    error_message = "instance_type should reach the instance."
  }
}

run "shell_job" {
  command = apply

  variables {
    job     = "shell"
    command = "echo hello > \"$RESULTS_DIR/hello.txt\""
  }

  assert {
    condition = contains(
      split("\n", aws_instance.runner.user_data),
      "echo hello > \"$RESULTS_DIR/hello.txt\"",
    )
    error_message = "A shell job should run the given command."
  }

  assert {
    condition = (
      !strcontains(aws_instance.runner.user_data, "demo-stacks generate") &&
      !strcontains(aws_instance.runner.user_data, "demo-stacks bench")
    )
    error_message = "A shell job should run only its own command."
  }
}

# The runner role is what a `shell` job's arbitrary code runs as, so its reach is
# pinned exactly: table-level S3 Tables actions on this bucket, and writes under this
# run's prefix only. Deleting namespaces or the bucket, or changing bucket policies,
# stays out on purpose.
run "runner_policy_is_scoped" {
  command = apply

  assert {
    condition = (
      [for s in data.aws_iam_policy_document.runner.statement : s.sid] == ["TableBucket", "Results"]
    )
    error_message = "The runner policy should have exactly the TableBucket and Results statements."
  }

  assert {
    condition = (
      data.aws_iam_policy_document.runner.statement[0].actions == toset([
        "s3tables:GetTableBucket",
        "s3tables:ListNamespaces",
        "s3tables:GetNamespace",
        "s3tables:CreateNamespace",
        "s3tables:ListTables",
        "s3tables:GetTable",
        "s3tables:CreateTable",
        "s3tables:DeleteTable",
        "s3tables:GetTableMetadataLocation",
        "s3tables:UpdateTableMetadataLocation",
        "s3tables:GetTableData",
        "s3tables:PutTableData",
      ])
    )
    error_message = "S3 Tables actions changed; check nothing destructive to the data root was added."
  }

  assert {
    condition = (
      data.aws_iam_policy_document.runner.statement[0].resources == toset([
        "arn:aws:s3tables:eu-west-2:111122223333:bucket/mock-tpch",
        "arn:aws:s3tables:eu-west-2:111122223333:bucket/mock-tpch/*",
      ])
    )
    error_message = "S3 Tables actions should be limited to the given table bucket."
  }

  assert {
    condition = (
      data.aws_iam_policy_document.runner.statement[1].actions == toset(["s3:PutObject"]) &&
      data.aws_iam_policy_document.runner.statement[1].resources == toset(["arn:aws:s3:::mock-results/runs/run-1/*"])
    )
    error_message = "The runner should only write objects under its own run prefix."
  }

  assert {
    condition = (
      aws_iam_role_policy.runner.role == aws_iam_role.runner.id &&
      aws_iam_role_policy.runner.policy == data.aws_iam_policy_document.runner.json
    )
    error_message = "The runner role should carry the runner policy document."
  }
}

run "shell_job_needs_a_command" {
  command = plan

  variables {
    job     = "shell"
    command = "   "
  }

  expect_failures = [aws_instance.runner]
}

run "rejects_unknown_job" {
  command = plan

  variables {
    job = "deploy"
  }

  expect_failures = [var.job]
}

run "rejects_uppercase_run_id" {
  command = plan

  variables {
    run_id = "Run-1"
  }

  expect_failures = [var.run_id]
}

run "rejects_run_id_starting_with_dash" {
  command = plan

  variables {
    run_id = "-run"
  }

  expect_failures = [var.run_id]
}

run "rejects_fractional_scale_factor" {
  command = plan

  variables {
    scale_factor = 2.5
  }

  expect_failures = [var.scale_factor]
}

run "rejects_zero_bench_repeats" {
  command = plan

  variables {
    bench_repeats = 0
  }

  expect_failures = [var.bench_repeats]
}
