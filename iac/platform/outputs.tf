output "state_bucket_name" {
  value = aws_s3_bucket.state.bucket
}

output "sessions_table_name" {
  value = aws_dynamodb_table.sessions.name
}

output "terraform_runner_project_name" {
  value = aws_codebuild_project.terraform_runner.name
}
