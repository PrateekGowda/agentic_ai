# Company Infrastructure Standards

These standards are used by the compliance agent and Terraform template renderer.

## Mandatory Controls

- All S3 buckets must enable server-side encryption with KMS.
- Public access must be blocked for all S3 buckets.
- Terraform state must use an encrypted S3 backend with locking.
- Every resource must include `Environment`, `Owner`, `CostCenter`, and `ManagedBy` tags.
- CloudWatch logging must be enabled for runtime services.
- IAM policies must avoid wildcard administrative permissions.
- High-severity compliance findings block production deployment.

## Recommended Controls

- Use GitHub OIDC instead of static AWS keys.
- Enable lifecycle policies for deployment artifacts and logs.
- Require pull request review before production applies.
