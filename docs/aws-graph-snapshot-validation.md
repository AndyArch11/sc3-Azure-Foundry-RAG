# AWS Graph Snapshot Validation

This runbook covers the current Milestone 4 relationship-graph rollout path on AWS.
The implementation is currently snapshot-backed: query web can publish graph build
artifacts to S3 and the AWS graph backend can read those artifacts back through
bucket/prefix configuration.

## Scope

Included in the current rollout:

- Publishing graph artifacts from `POST /api/graph/build` to S3
- Reading graph snapshot artifacts from S3 through `GRAPH_BACKEND=aws`
- Verifying export, node lookup, and related-subgraph API responses
- Local preflight validation of the smoke payload before contacting a deployed endpoint

Not yet included:

- Fully AWS-native graph persistence/query semantics
- Incremental graph mutation workflows
- Performance validation under production-scale graph sizes

## Required Configuration

### AWS Graph Snapshot Reads

```bash
export GRAPH_ENABLED=true
export GRAPH_BACKEND=aws
export GRAPH_AWS_ARTIFACTS_BUCKET="<s3-bucket-name>"
export GRAPH_AWS_ARTIFACTS_PREFIX="<optional/prefix>"
```

### AWS Graph Snapshot Publishing

```bash
export GRAPH_AWS_PUBLISH_ENABLED=true
export GRAPH_AWS_ARTIFACTS_BUCKET="<s3-bucket-name>"
export GRAPH_AWS_ARTIFACTS_PREFIX="<optional/prefix>"
```

Notes:

- `GRAPH_AWS_ARTIFACTS_BUCKET` falls back to `S3_BUCKET_NAME` when unset, but an explicit graph bucket is preferred.
- Runtime access uses the existing ECS task credential/session path and the repo S3 storage abstraction.

## Published Objects

When publishing is enabled, graph build writes these objects to the configured
bucket/prefix:

- `nodes.jsonl`
- `edges.jsonl`
- `graph_build_report.json`

Example effective keys with `GRAPH_AWS_ARTIFACTS_PREFIX=graph/dev`:

- `graph/dev/nodes.jsonl`
- `graph/dev/edges.jsonl`
- `graph/dev/graph_build_report.json`

## Manual Validation Flow

1. Trigger a graph build:

```bash
curl -X POST http://localhost:8080/api/graph/build \
  -H 'Content-Type: application/json' \
  -d '{
    "auth_token": "<token>",
    "controls": [...],
    "chunks": [...],
    "persist_store": false
  }'
```

2. Confirm the response includes `report.published_snapshot`.

3. Verify objects exist in S3:

```bash
aws s3 ls "s3://${GRAPH_AWS_ARTIFACTS_BUCKET}/${GRAPH_AWS_ARTIFACTS_PREFIX}/"
```

4. Query through AWS backend mode:

```bash
curl "http://localhost:8080/api/graph/export?auth_token=<token>&format=json"
curl "http://localhost:8080/api/graph/nodes/<node-id>?auth_token=<token>"
curl "http://localhost:8080/api/graph/related?auth_token=<token>&node_id=<node-id>&depth=2"
```

## Smoke Script

Use the repeatable smoke script for private-network or operator-driven validation:

```bash
./ops/scripts/aws/run-query-web-graph-smoke.sh "https://<query-web-fqdn>" "<token>"
```

Required environment:

```bash
export QUERY_GRAPH_NODE_ID="a:nist-csf-gv-gv-oc-01"
```

### Build + Publish Validation

```bash
cp ./ops/scripts/aws/graph-smoke-payload.sample.json /tmp/graph-smoke-payload.json
export QUERY_GRAPH_BUILD_PAYLOAD_FILE=/tmp/graph-smoke-payload.json
./ops/scripts/aws/run-query-web-graph-smoke.sh "https://<query-web-fqdn>" "<token>"
```

### Local Preflight-Only Validation

```bash
QUERY_GRAPH_PREFLIGHT_ONLY=true \
QUERY_GRAPH_BUILD_PAYLOAD_FILE=./ops/scripts/aws/graph-smoke-payload.sample.json \
QUERY_GRAPH_NODE_ID="a:nist-csf-gv-gv-oc-01" \
./ops/scripts/aws/run-query-web-graph-smoke.sh
```

The smoke script performs:

- `/health` preflight
- local payload contract validation when a build payload is provided
- optional `POST /api/graph/build`
- `GET /api/graph/export`
- `GET /api/graph/nodes/{id}`
- `GET /api/graph/related`

## Current Limitation

This is an AWS snapshot-backed delivery model, not yet a fully AWS-native graph
persistence/query implementation. The current rollout proves:

- S3-hosted graph artifact publication
- S3-backed snapshot reads through the AWS graph backend
- API response-shape parity with the local baseline
- Repeatable operator smoke validation

It does not yet prove:

- native AWS graph storage semantics
- large-scale incremental update behaviour
- AWS-specific performance characteristics under production load
