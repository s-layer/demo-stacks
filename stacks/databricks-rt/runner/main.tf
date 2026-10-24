# A one-shot EC2 runner for benchmarking a Databricks warehouse from inside AWS. It
# boots, runs one command, uploads its log and results to the data stack's bucket,
# and powers off. Shutdown behaviour is "terminate", so a finished (or stuck, after
# max_lifetime_minutes) runner stops billing even if nobody runs `terraform destroy`.
# Destroy afterwards to remove the IAM role, security group and token parameter.
#
# The runner needs no Databricks-side AWS access: it reaches the warehouse over the
# public Statement Execution API with a personal access token.

data "aws_ssm_parameter" "al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

# The default VPC's public subnets give the runner internet egress (PyPI, GitHub,
# the workspace) without a NAT gateway, which would bill by the hour.
data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
  filter {
    name   = "default-for-az"
    values = ["true"]
  }
}

resource "aws_security_group" "runner" {
  name_prefix = "demo-stacks-dbx-runner-"
  description = "Egress only; no inbound access. Use SSM Session Manager to look inside."
  vpc_id      = data.aws_vpc.default.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_ssm_parameter" "token" {
  name  = "/demo-stacks/dbx-runner/${var.run_id}"
  type  = "SecureString"
  value = var.databricks_token
}

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "runner" {
  statement {
    sid       = "Token"
    actions   = ["ssm:GetParameter"]
    resources = [aws_ssm_parameter.token.arn]
  }
  statement {
    sid       = "Results"
    actions   = ["s3:PutObject"]
    resources = ["arn:aws:s3:::${var.results_bucket}/results/runs/${var.run_id}/*"]
  }
}

resource "aws_iam_role" "runner" {
  name_prefix        = "demo-stacks-dbx-runner-"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

resource "aws_iam_role_policy" "runner" {
  role   = aws_iam_role.runner.id
  policy = data.aws_iam_policy_document.runner.json
}

resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.runner.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "runner" {
  name_prefix = "demo-stacks-dbx-runner-"
  role        = aws_iam_role.runner.name
}

resource "aws_instance" "runner" {
  ami                                  = data.aws_ssm_parameter.al2023.value
  instance_type                        = var.instance_type
  subnet_id                            = data.aws_subnets.default.ids[0]
  vpc_security_group_ids               = [aws_security_group.runner.id]
  iam_instance_profile                 = aws_iam_instance_profile.runner.name
  associate_public_ip_address          = true
  instance_initiated_shutdown_behavior = "terminate"

  metadata_options {
    http_tokens = "required"
  }

  root_block_device {
    volume_size           = var.volume_size_gb
    volume_type           = "gp3"
    delete_on_termination = true
  }

  user_data_replace_on_change = true
  user_data = templatefile("${path.module}/user_data.sh.tftpl", {
    region               = var.region
    databricks_host      = var.databricks_host
    token_parameter      = aws_ssm_parameter.token.name
    results_bucket       = var.results_bucket
    run_id               = var.run_id
    repo_url             = var.repo_url
    repo_ref             = var.repo_ref
    command              = var.command
    max_lifetime_minutes = var.max_lifetime_minutes
  })

  tags = {
    Name = "demo-stacks-dbx-runner-${var.run_id}"
  }
}
