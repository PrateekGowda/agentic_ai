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

resource "aws_codebuild_project" "agent_image" {
  name         = "agentcore-deployer-${var.environment}-agent-image"
  service_role = aws_iam_role.image_builder.arn

  artifacts {
    type = "NO_ARTIFACTS"
  }

  environment {
    compute_type                = "BUILD_GENERAL1_MEDIUM"
    image                       = "aws/codebuild/amazonlinux2-aarch64-standard:3.0"
    type                        = "ARM_CONTAINER"
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
      name  = "AGENT_RUNTIME_REPOSITORY_URI"
      value = aws_ecr_repository.agent_runtime.repository_url
    }
  }

  source {
    type      = "NO_SOURCE"
    buildspec = file("${path.module}/build-agent.yml")
  }
}

resource "aws_iam_role" "agentcore_runtime" {
  name = "agentcore-deployer-${var.environment}-agentcore-runtime"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AssumeRolePolicy"
        Effect = "Allow"
        Principal = {
          Service = "bedrock-agentcore.amazonaws.com"
        }
        Action = "sts:AssumeRole"
        Condition = {
          StringEquals = {
            "aws:SourceAccount" = data.aws_caller_identity.current.account_id
          }
          ArnLike = {
            "aws:SourceArn" = "arn:aws:bedrock-agentcore:${var.region}:${data.aws_caller_identity.current.account_id}:*"
          }
        }
      }
    ]
  })
}

resource "aws_iam_role_policy" "agentcore_runtime" {
  name = "agentcore-deployer-${var.environment}-agentcore-runtime"
  role = aws_iam_role.agentcore_runtime.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ECRImageAccess"
        Effect = "Allow"
        Action = [
          "ecr:BatchGetImage",
          "ecr:GetDownloadUrlForLayer"
        ]
        Resource = [
          aws_ecr_repository.agent_runtime.arn
        ]
      },
      {
        Sid      = "ECRTokenAccess"
        Effect   = "Allow"
        Action   = "ecr:GetAuthorizationToken"
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "logs:DescribeLogStreams",
          "logs:CreateLogGroup"
        ]
        Resource = "arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/bedrock-agentcore/runtimes/*"
      },
      {
        Effect   = "Allow"
        Action   = "logs:DescribeLogGroups"
        Resource = "arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:*"
      },
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/bedrock-agentcore/runtimes/*:log-stream:*"
      },
      {
        Effect = "Allow"
        Action = [
          "xray:PutTraceSegments",
          "xray:PutTelemetryRecords",
          "xray:GetSamplingRules",
          "xray:GetSamplingTargets"
        ]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = "cloudwatch:PutMetricData"
        Resource = "*"
        Condition = {
          StringEquals = {
            "cloudwatch:namespace" = "bedrock-agentcore"
          }
        }
      },
      {
        Sid    = "BedrockModelInvocation"
        Effect = "Allow"
        Action = [
          "bedrock:InvokeModel",
          "bedrock:InvokeModelWithResponseStream"
        ]
        Resource = [
          "arn:aws:bedrock:*::foundation-model/*",
          "arn:aws:bedrock:${var.region}:${data.aws_caller_identity.current.account_id}:*"
        ]
      }
    ]
  })
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
          GITHUB_TOKEN_SECRET_ARN           = var.github_token_secret_arn
          REFERENCE_LIBRARY_REPO            = var.reference_library_repo
          BEDROCK_MODEL_ID                  = var.bedrock_model_id
          AGENT_LLM_ENABLED                 = tostring(var.agent_llm_enabled)
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

resource "aws_security_group" "ecs_app" {
  count       = var.deploy_ecs ? 1 : 0
  name        = "agentcore-deployer-${var.environment}-ecs-app"
  description = "Allow public test access to AgentCore deployer UI and API"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description = "Web UI"
    from_port   = 3000
    to_port     = 3000
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "Backend API"
    from_port   = 8000
    to_port     = 8000
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_ecs_cluster" "app" {
  count = var.deploy_ecs ? 1 : 0
  name  = "agentcore-deployer-${var.environment}"
}

resource "aws_iam_role" "ecs_task_execution" {
  count = var.deploy_ecs ? 1 : 0
  name  = "agentcore-deployer-${var.environment}-ecs-execution"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ecs-tasks.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "ecs_task_execution" {
  count      = var.deploy_ecs ? 1 : 0
  role       = aws_iam_role.ecs_task_execution[0].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role" "ecs_task" {
  count = var.deploy_ecs ? 1 : 0
  name  = "agentcore-deployer-${var.environment}-ecs-task"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ecs-tasks.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy" "ecs_task" {
  count = var.deploy_ecs ? 1 : 0
  name  = "agentcore-deployer-${var.environment}-ecs-task"
  role  = aws_iam_role.ecs_task[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "*"
        ]
        Resource = "*"
      }
    ]
  })
}

resource "aws_ecs_task_definition" "app" {
  count                    = var.deploy_ecs ? 1 : 0
  family                   = "agentcore-deployer-${var.environment}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.ecs_task_cpu
  memory                   = var.ecs_task_memory
  execution_role_arn       = aws_iam_role.ecs_task_execution[0].arn
  task_role_arn            = aws_iam_role.ecs_task[0].arn

  container_definitions = jsonencode([
    {
      name      = "backend"
      image     = "${aws_ecr_repository.backend.repository_url}:${var.image_tag}"
      essential = true
      portMappings = [
        {
          containerPort = 8000
          hostPort      = 8000
          protocol      = "tcp"
        }
      ]
      environment = [
        { name = "APP_ENV", value = var.environment },
        { name = "AWS_REGION", value = var.region },
        { name = "GITHUB_OWNER", value = var.github_owner },
        { name = "GITHUB_TOKEN_SECRET_ARN", value = var.github_token_secret_arn },
        { name = "REFERENCE_LIBRARY_REPO", value = var.reference_library_repo },
        { name = "BEDROCK_MODEL_ID", value = var.bedrock_model_id },
        { name = "AGENT_LLM_ENABLED", value = tostring(var.agent_llm_enabled) },
        { name = "PROJECT_STATE_BUCKET", value = "hack-aib-tf-backend" },
        { name = "COMPANY_STANDARDS_PATH", value = "/app/samples/company-standards.md" },
        { name = "AGENTCORE_REQUIREMENT_RUNTIME_ARN", value = var.agentcore_requirement_runtime_arn },
        { name = "AGENTCORE_PROVISIONER_RUNTIME_ARN", value = var.agentcore_provisioner_runtime_arn },
        { name = "AGENTCORE_DEPLOYER_RUNTIME_ARN", value = var.agentcore_deployer_runtime_arn },
        { name = "AGENTCORE_COMPLIANCE_RUNTIME_ARN", value = var.agentcore_compliance_runtime_arn },
        { name = "AGENTCORE_MEMORY_ID", value = var.agentcore_memory_id }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.orchestrator.name
          awslogs-region        = var.region
          awslogs-stream-prefix = "backend"
        }
      }
    },
    {
      name      = "web"
      image     = "${aws_ecr_repository.web.repository_url}:${var.image_tag}"
      essential = true
      dependsOn = [
        {
          containerName = "backend"
          condition     = "START"
        }
      ]
      portMappings = [
        {
          containerPort = 3000
          hostPort      = 3000
          protocol      = "tcp"
        }
      ]
      environment = [
        { name = "ORCHESTRATOR_BASE_URL", value = "http://127.0.0.1:8000" }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.orchestrator.name
          awslogs-region        = var.region
          awslogs-stream-prefix = "web"
        }
      }
    }
  ])
}

resource "aws_ecs_service" "app" {
  count           = var.deploy_ecs ? 1 : 0
  name            = "agentcore-deployer-${var.environment}"
  cluster         = aws_ecs_cluster.app[0].id
  task_definition = aws_ecs_task_definition.app[0].arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    assign_public_ip = true
    security_groups  = [aws_security_group.ecs_app[0].id]
    subnets          = data.aws_subnets.default.ids
  }

  depends_on = [
    aws_iam_role_policy_attachment.ecs_task_execution
  ]
}

# AgentCore runtime resources are intentionally isolated behind this module boundary.
# Add the current AgentCore provider resources here once the AWS account has preview access.
