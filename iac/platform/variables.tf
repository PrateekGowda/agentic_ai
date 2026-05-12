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
