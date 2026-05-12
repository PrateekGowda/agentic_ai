output "state_bucket_name" {
  value = aws_s3_bucket.state.bucket
}

output "sessions_table_name" {
  value = aws_dynamodb_table.sessions.name
}

output "terraform_runner_project_name" {
  value = aws_codebuild_project.terraform_runner.name
}

output "backend_repository_url" {
  value = aws_ecr_repository.backend.repository_url
}

output "web_repository_url" {
  value = aws_ecr_repository.web.repository_url
}

output "agent_runtime_repository_url" {
  value = aws_ecr_repository.agent_runtime.repository_url
}

output "backend_image_build_project_name" {
  value = aws_codebuild_project.backend_image.name
}

output "web_image_build_project_name" {
  value = aws_codebuild_project.web_image.name
}

output "backend_service_url" {
  value = var.deploy_services ? aws_apprunner_service.backend[0].service_url : null
}

output "web_service_url" {
  value = var.deploy_services ? aws_apprunner_service.web[0].service_url : null
}

output "ecs_cluster_name" {
  value = var.deploy_ecs ? aws_ecs_cluster.app[0].name : null
}

output "ecs_service_name" {
  value = var.deploy_ecs ? aws_ecs_service.app[0].name : null
}

output "ecs_security_group_id" {
  value = var.deploy_ecs ? aws_security_group.ecs_app[0].id : null
}
