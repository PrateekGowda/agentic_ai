# Policy Checks

Generated Terraform must pass these checks before apply:

- `terraform fmt -check`
- `terraform validate`
- `checkov -d terraform`
- `opa eval --data policies --input terraform-plan.json data.company.terraform.deny`

High or critical findings block deployment until remediated.
