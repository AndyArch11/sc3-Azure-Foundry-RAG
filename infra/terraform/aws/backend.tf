# Remote state stored in S3 + DynamoDB lock table.
# The S3 bucket and lock table must be created out-of-band (bootstrap) before
# running terraform init here.
#
# terraform init \
#   -backend-config="bucket=<tfstate-bucket>" \
#   -backend-config="key=aws/<environment>/terraform.tfstate" \
#   -backend-config="region=<aws-region>" \
#   -backend-config="dynamodb_table=<lock-table>"

terraform {
  backend "s3" {}
}
