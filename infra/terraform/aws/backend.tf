# Remote state stored in S3 with lockfile-based state locking.
# The S3 bucket must be created out-of-band (bootstrap) before
# running terraform init here.
#
# terraform init \
#   -backend-config="bucket=<tfstate-bucket>" \
#   -backend-config="key=aws/<environment>/terraform.tfstate" \
#   -backend-config="region=<aws-region>" \
#   -backend-config="use_lockfile=true"

terraform {
  backend "s3" {}
}
