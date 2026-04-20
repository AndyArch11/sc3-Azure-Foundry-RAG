# Bootstrap Stack

Creates the S3 remote state bucket, DynamoDB lock table, and optional Secrets Manager secret that must exist before the main environment stack can be initialised.

## Purpose

This stack creates:

- S3 bucket for Terraform remote state (versioning enabled, AES256 SSE, HTTPS-only bucket policy, `prevent_destroy` lifecycle)
- DynamoDB table for Terraform state locking (`LockID` hash key, PAY_PER_REQUEST, SSE, `prevent_destroy` lifecycle)
- Optional Secrets Manager secret with a placeholder `auth_token` value for bootstrap-time convenience

Its outputs are consumed by the environment-level `backend.hcl` files that configure the S3 backend for the main stack.

Note: The bootstrap Secrets Manager secret is a standalone deployment convenience. In production environments, secrets are expected to be managed through your organisation's secret lifecycle controls after the initial deployment.

Note: The S3 bucket and DynamoDB table are protected against accidental deletion by both Terraform `prevent_destroy` and S3 versioning. Removing the bucket requires an explicit, deliberate unlock step.

## Inputs

- `aws_region`: AWS region for all bootstrap resources.
- `project`: Project name prefix used in resource naming.
- `environment`: Environment name (`dev`/`test`/`prod`).
- `enable_bootstrap_secrets_manager`: Toggle for optional Secrets Manager secret creation.

## Execute

Run from `infra/terraform/aws/bootstrap/`:

```bash
terraform init
terraform apply -var-file=terraform.tfvars
```

After apply, note the outputs and supply them to `terraform init` for the main stack:

```bash
cd ../
terraform init \
  -backend-config="bucket=$(terraform -chdir=bootstrap output -raw state_bucket_name)" \
  -backend-config="key=aws/<environment>/terraform.tfstate" \
  -backend-config="region=<aws_region>" \
  -backend-config="dynamodb_table=$(terraform -chdir=bootstrap output -raw lock_table_name)"
```

## Teardown

Destroy bootstrap only after the main environment stack has already been destroyed.

Typical order:

1. Destroy the root environment stack (`terraform -chdir=infra/terraform/aws destroy`)
2. Empty the S3 bucket (versioned objects must be deleted first)
3. Remove Terraform `prevent_destroy` annotations if needed
4. Destroy the bootstrap stack (`terraform -chdir=infra/terraform/aws/bootstrap destroy`)
