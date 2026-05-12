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

resource "aws_ecr_repository" "backend" {
  name                 = "agentcore-deployer-${var.environment}-orchestrator"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_repository" "web" {
  name                 = "agentcore-deployer-${var.environment}-web"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_repository" "agent_runtime" {
  name                 = "agentcore-deployer-${var.environment}-agent-runtime"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
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

resource "aws_iam_role_policy" "terraform_runner" {
  name = "agentcore-deployer-${var.environment}-terraform-runner"
  role = aws_iam_role.terraform_runner.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "s3:*",
          "dynamodb:*",
          "kms:*",
          "cloudwatch:*",
          "iam:*",
          "codebuild:*",
          "ecr:*",
          "apprunner:*",
          "secretsmanager:*",
          "sts:GetCallerIdentity"
        ]
        Resource = "*"
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

resource "aws_iam_role" "image_builder" {
  name = "agentcore-deployer-${var.environment}-image-builder"

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

resource "aws_iam_role_policy" "image_builder" {
  name = "agentcore-deployer-${var.environment}-image-builder"
  role = aws_iam_role.image_builder.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "ecr:GetAuthorizationToken"
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:CompleteLayerUpload",
          "ecr:InitiateLayerUpload",
          "ecr:PutImage",
          "ecr:UploadLayerPart"
        ]
        Resource = [
          aws_ecr_repository.backend.arn,
          aws_ecr_repository.web.arn,
          aws_ecr_repository.agent_runtime.arn
        ]
      }
    ]
  })
}

resource "aws_codebuild_project" "backend_image" {
  name         = "agentcore-deployer-${var.environment}-backend-image"
  service_role = aws_iam_role.image_builder.arn

  artifacts {
    type = "NO_ARTIFACTS"
  }

  environment {
    compute_type                = "BUILD_GENERAL1_MEDIUM"
    image                       = "aws/codebuild/standard:7.0"
    type                        = "LINUX_CONTAINER"
    privileged_mode             = true
    image_pull_credentials_type = "CODEBUILD"

    environment_variable {
      name  = "AWS_ACCOUNT_ID"
      value = data.aws_caller_identity.current.account_id
    }

    environment_variable {
      name  = "REPO_URL"
      value = var.github_repo_url
    }

    environment_variable {
      name  = "IMAGE_TAG"
      value = var.image_tag
    }

    environment_variable {
      name  = "BACKEND_REPOSITORY_URI"
      value = aws_ecr_repository.backend.repository_url
    }
  }

  source {
    type      = "NO_SOURCE"
    buildspec = file("${path.module}/build-backend.yml")
  }
}

resource "aws_codebuild_project" "web_image" {
  name         = "agentcore-deployer-${var.environment}-web-image"
  service_role = aws_iam_role.image_builder.arn

  artifacts {
    type = "NO_ARTIFACTS"
  }

  environment {
    compute_type                = "BUILD_GENERAL1_MEDIUM"
    image                       = "aws/codebuild/standard:7.0"
    type                        = "LINUX_CONTAINER"
    privileged_mode             = true
    image_pull_credentials_type = "CODEBUILD"

    environment_variable {
      name  = "AWS_ACCOUNT_ID"
      value = data.aws_caller_identity.current.account_id
    }

    environment_variable {
      name  = "REPO_URL"
      value = var.github_repo_url
    }

    environment_variable {
      name  = "IMAGE_TAG"
      value = var.image_tag
    }

    environment_variable {
      name  = "WEB_REPOSITORY_URI"
      value = aws_ecr_repository.web.repository_url
    }
  }

  source {
    type      = "NO_SOURCE"
    buildspec = file("${path.module}/build-web.yml")
  }
}

resource "aws_iam_role" "apprunner_access" {
  name = "agentcore-deployer-${var.environment}-apprunner-access"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "build.apprunner.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "apprunner_ecr" {
  role       = aws_iam_role.apprunner_access.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSAppRunnerServicePolicyForECRAccess"
}

resource "aws_iam_role" "apprunner_instance" {
  name = "agentcore-deployer-${var.environment}-apprunner-instance"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "tasks.apprunner.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy" "apprunner_instance" {
  name = "agentcore-deployer-${var.environment}-apprunner-instance"
  role = aws_iam_role.apprunner_instance.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
          "sts:GetCallerIdentity",
          "bedrock-agentcore:*"
        ]
        Resource = "*"
      }
    ]
  })
}

resource "aws_apprunner_service" "backend" {
  count        = var.deploy_services ? 1 : 0
  service_name = "agentcore-deployer-${var.environment}-backend"

  source_configuration {
    authentication_configuration {
      access_role_arn = aws_iam_role.apprunner_access.arn
    }

    image_repository {
      image_identifier      = "${aws_ecr_repository.backend.repository_url}:${var.image_tag}"
      image_repository_type = "ECR"

      image_configuration {
        port = "8000"
        runtime_environment_variables = {
          APP_ENV                           = var.environment
          AWS_REGION                        = var.region
          GITHUB_OWNER                      = var.github_owner
          COMPANY_STANDARDS_PATH            = "/app/samples/company-standards.md"
          AGENTCORE_REQUIREMENT_RUNTIME_ARN = ""
          AGENTCORE_PROVISIONER_RUNTIME_ARN = ""
          AGENTCORE_DEPLOYER_RUNTIME_ARN    = ""
          AGENTCORE_COMPLIANCE_RUNTIME_ARN  = ""
        }
      }
    }
  }

  instance_configuration {
    cpu               = "1024"
    memory            = "2048"
    instance_role_arn = aws_iam_role.apprunner_instance.arn
  }
}

resource "aws_apprunner_service" "web" {
  count        = var.deploy_services ? 1 : 0
  service_name = "agentcore-deployer-${var.environment}-web"

  source_configuration {
    authentication_configuration {
      access_role_arn = aws_iam_role.apprunner_access.arn
    }

    image_repository {
      image_identifier      = "${aws_ecr_repository.web.repository_url}:${var.image_tag}"
      image_repository_type = "ECR"

      image_configuration {
        port = "3000"
        runtime_environment_variables = {
          ORCHESTRATOR_BASE_URL = "https://${aws_apprunner_service.backend[0].service_url}"
        }
      }
    }
  }

  instance_configuration {
    cpu    = "1024"
    memory = "2048"
  }
}

# AgentCore runtime resources are intentionally isolated behind this module boundary.
# Add the current AgentCore provider resources here once the AWS account has preview access.
