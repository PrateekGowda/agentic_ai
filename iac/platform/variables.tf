variable "region" {
  type        = string
  description = "AWS region for the platform."
  default     = "us-east-1"
}

variable "environment" {
  type        = string
  description = "Platform environment."
  default     = "dev"
}

variable "owner" {
  type        = string
  description = "Owning team or email."
}

variable "github_owner" {
  type        = string
  description = "GitHub organization or user that receives generated repositories."
}

variable "reference_library_repo" {
  type        = string
  description = "GitHub repository name used as the reusable Terraform reference library."
  default     = "iac-codebase-agentic-ai"
}

variable "agentcore_runtime_image_uri" {
  type        = string
  description = "Container image URI used by AgentCore runtimes."
}

variable "github_repo_url" {
  type        = string
  description = "GitHub repository URL used by cloud image builders."
  default     = "https://github.com/PrateekGowda/agentic_ai.git"
}

variable "image_tag" {
  type        = string
  description = "Container image tag deployed to App Runner."
  default     = "latest"
}

variable "deploy_services" {
  type        = bool
  description = "Set true after images are pushed to ECR so App Runner can deploy them."
  default     = false
}

variable "deploy_ecs" {
  type        = bool
  description = "Set true to deploy the application on ECS Fargate."
  default     = false
}

variable "github_token_secret_arn" {
  type        = string
  description = "Optional Secrets Manager ARN containing a GitHub token or GitHub App installation token."
  default     = ""
}

variable "agentcore_requirement_runtime_arn" {
  type        = string
  description = "Optional AgentCore Runtime ARN for the requirement gathering agent."
  default     = ""
}

variable "agentcore_provisioner_runtime_arn" {
  type        = string
  description = "Optional AgentCore Runtime ARN for the code provisioning agent."
  default     = ""
}

variable "agentcore_deployer_runtime_arn" {
  type        = string
  description = "Optional AgentCore Runtime ARN for the deployment agent."
  default     = ""
}

variable "agentcore_compliance_runtime_arn" {
  type        = string
  description = "Optional AgentCore Runtime ARN for the compliance agent."
  default     = ""
}

variable "agentcore_memory_id" {
  type        = string
  description = "Optional AgentCore Memory ID used to persist chat context."
  default     = ""
}
