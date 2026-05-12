package company.terraform

deny[msg] {
  resource := input.resource_changes[_]
  resource.type == "aws_s3_bucket_public_access_block"
  after := resource.change.after
  not after.block_public_policy
  msg := sprintf("S3 bucket public policies must be blocked for %s", [resource.address])
}

deny[msg] {
  resource := input.resource_changes[_]
  resource.type == "aws_kms_key"
  not resource.change.after.enable_key_rotation
  msg := sprintf("KMS key rotation must be enabled for %s", [resource.address])
}

deny[msg] {
  resource := input.resource_changes[_]
  tags := resource.change.after.tags
  required := {"Environment", "Owner", "CostCenter", "ManagedBy"}
  missing := required - {key | tags[key]}
  count(missing) > 0
  msg := sprintf("%s is missing mandatory tags: %v", [resource.address, missing])
}
