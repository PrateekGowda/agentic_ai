resource "aws_kms_key" "platform" {
  description             = "AgentCore deployer platform key"
  deletion_window_in_days = 7
  enable_key_rotation     = true
}

resource "aws_s3_bucket" "state" {
  bucket_prefix = "agentcore-deployer-${var.environment}-state-"
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    apply_server_side_encryption_by_default {
      kms_master_key_id = aws_kms_key.platform.arn
      sse_algorithm     = "aws:kms"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_dynamodb_table" "sessions" {
  name         = "agentcore-deployer-${var.environment}-sessions"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  range_key    = "sk"

  attribute {
    name = "pk"
    type = "S"
  }

  attribute {
    name = "sk"
    type = "S"
  }

  server_side_encryption {
    enabled     = true
    kms_key_arn = aws_kms_key.platform.arn
  }
}

resource "aws_cloudwatch_log_group" "orchestrator" {
  name              = "/agentcore-deployer/${var.environment}/orchestrator"
  retention_in_days = 30
  kms_key_id        = aws_kms_key.platform.arn
}

resource "aws_iam_role" "terraform_runner" {
  name = "agentcore-deployer-${var.environment}-terraform-runner"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "codebuild.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_codebuild_project" "terraform_runner" {
  name          = "agentcore-deployer-${var.environment}-terraform-runner"
  service_role  = aws_iam_role.terraform_runner.arn
  build_timeout = 60

  artifacts {
    type = "NO_ARTIFACTS"
  }

  environment {
    compute_type                = "BUILD_GENERAL1_MEDIUM"
    image                       = "aws/codebuild/standard:7.0"
    type                        = "LINUX_CONTAINER"
    image_pull_credentials_type = "CODEBUILD"
  }

  logs_config {
    cloudwatch_logs {
      group_name = aws_cloudwatch_log_group.orchestrator.name
      status     = "ENABLED"
    }
  }

  source {
    type      = "NO_SOURCE"
    buildspec = file("${path.module}/runner-buildspec.yml")
  }
}

# AgentCore runtime resources are intentionally isolated behind this module boundary.
# Add the current AgentCore provider resources here once the AWS account has preview access.
