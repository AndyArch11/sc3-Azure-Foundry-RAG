# AGENT.md — AWS Terraform

## Scope

AWS infrastructure provisioning and lifecycle management via Terraform.

## Working Directory

infra/terraform/aws/

## Bootstrap First

Before running `terraform init` on the main stack, provision remote state backend:

```bash
cd infra/terraform/aws/bootstrap
terraform init
terraform apply -var="aws_region=ap-southeast-2" -var="project=rag" -var="environment=dev"
```

Note the outputs (`state_bucket_name`, `lock_table_name`) and use them in `terraform init`:

```bash
cd infra/terraform/aws
terraform init \
  -backend-config="bucket=<state_bucket_name>" \
  -backend-config="key=aws/dev/terraform.tfstate" \
  -backend-config="region=ap-southeast-2" \
  -backend-config="use_lockfile=true"
```

## Validation Before Apply

```bash
terraform fmt -recursive modules main.tf
terraform validate
terraform plan -var-file=environments/dev/dev.tfvars
```

## Typical Apply Patterns

**Full stack:**
```bash
terraform apply -var-file=environments/dev/dev.tfvars -auto-approve
```

**Module-scoped:**
```bash
terraform apply -var-file=environments/dev/dev.tfvars -target=module.identity
terraform apply -var-file=environments/dev/dev.tfvars -target=module.app_hosting
```

## Module Map

| Module             | Purpose                                                     |
|--------------------|-------------------------------------------------------------|
| `network`          | VPC, subnets, NAT gateways, security groups, VPC endpoints  |
| `observability`    | CloudWatch log groups                                       |
| `data_services`    | S3, OpenSearch, DynamoDB                                    |
| `identity`         | ECS task execution role, ECS task role with all policies    |
| `container_registry` | ECR repositories for query-web and ingestion             |
| `app_secrets`      | Secrets Manager secret for runtime credentials              |
| `app_hosting`      | ECS cluster, Fargate task definitions, services             |

## Deliberate Differences from Azure Stack

The following Azure modules have no direct AWS counterpart by design:

- `foundation` — AWS has no resource group concept; tags fulfil this role
- `dns` — VPC DNS resolver handles private resolution; Route53 private zones can be layered on later
- `private_endpoints` — AWS VPC endpoints are provisioned inside `network`
- `bastion_jumpbox` — Use AWS Systems Manager Session Manager for interactive access
- `foundry` — Azure AI Foundry is Azure-specific; AWS uses Bedrock (serverless, no project construct)
