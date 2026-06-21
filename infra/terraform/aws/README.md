# AWS Terraform Stack

Provisions the AWS infrastructure for the compliance RAG platform using ECS Fargate, OpenSearch, S3, DynamoDB, and Bedrock.

## Repository Layout

```
aws/
├── bootstrap/          # Phase 0: remote state S3 bucket + DynamoDB lock table + Secrets Manager
├── environments/
│   ├── dev/dev.tfvars
│   ├── test/test.tfvars
│   └── prod/prod.tfvars
├── modules/
│   ├── network/        # VPC, subnets, NAT gateways, security groups, VPC endpoints
│   ├── observability/  # CloudWatch log groups
│   ├── data_services/  # S3, OpenSearch Service, DynamoDB
│   ├── identity/       # ECS task execution + task IAM roles
│   ├── container_registry/  # ECR repositories
│   ├── app_secrets/    # Secrets Manager runtime secrets
│   └── app_hosting/    # ECS cluster, Fargate task definitions and services
├── main.tf             # Root module wiring
├── variables.tf
├── outputs.tf
├── locals.tf
├── backend.tf          # Partial S3 backend (supplied at init time)
├── providers.tf
└── versions.tf
```

## Prerequisites

- AWS CLI configured for the target account
- Terraform >= 1.6.0
- AWS provider ~5.0 (downloaded by `terraform init`)

## Phase 0: Bootstrap Remote State

Create the S3 + DynamoDB backend **once per environment** before running the main stack:

```bash
cd infra/terraform/aws/bootstrap

terraform init
terraform apply \
  -var="aws_region=ap-southeast-2" \
  -var="project=rag" \
  -var="environment=dev"
```

Note the outputs:

```bash
terraform output state_bucket_name
terraform output lock_table_name
```

## Phase 1: Main Stack

```bash
cd infra/terraform/aws

terraform init \
  -backend-config="bucket=<state_bucket_name>" \
  -backend-config="key=aws/dev/terraform.tfstate" \
  -backend-config="region=ap-southeast-2" \
  -backend-config="use_lockfile=true"

terraform plan  -var-file=environments/dev/dev.tfvars
terraform apply -var-file=environments/dev/dev.tfvars
```

## Module Alignment with Azure Stack

| Azure module       | AWS equivalent                  | Notes                                                     |
|--------------------|---------------------------------|-----------------------------------------------------------|
| `foundation`       | *(absent)*                      | AWS has no resource group concept; tags serve this role   |
| `network`          | `network`                       | VPC ↔ VNet; VPC endpoints baked in rather than separate   |
| `dns`              | *(absent)*                      | VPC DNS resolver handles private resolution natively      |
| `observability`    | `observability`                 | CloudWatch ↔ Log Analytics                               |
| `data_services`    | `data_services`                 | S3+OpenSearch+DynamoDB ↔ StorageAccount+AISearch+Cosmos   |
| `identity`         | `identity`                      | IAM roles ↔ Managed Identity                             |
| `private_endpoints`| *(inside `network`)*            | VPC interface/gateway endpoints provisioned in `network`  |
| `app_secrets`      | `app_secrets`                   | Secrets Manager ↔ Key Vault                              |
| `agent_hosting`    | `app_hosting`                   | ECS Fargate ↔ Container Apps Environment                 |
| `container_registry`| *(inside `data_services` on Azure)* | ECR as a dedicated module on AWS                    |
| `bastion_jumpbox`  | *(absent)*                      | Use SSM Session Manager instead of an EC2 bastion         |
| `foundry`          | *(absent)*                      | Azure AI Foundry is Azure-specific; Bedrock is serverless |
