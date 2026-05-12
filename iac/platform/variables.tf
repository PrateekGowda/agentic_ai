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
