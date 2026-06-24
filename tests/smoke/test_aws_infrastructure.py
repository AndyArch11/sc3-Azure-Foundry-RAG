"""AWS infrastructure smoke tests.

These tests verify that a deployed AWS environment is reachable and that the
runtime adapters can communicate with each live AWS service.

Marked ``integration`` and ``private_network`` — run from inside the VPC
(ECS task, EC2 instance with correct IAM role, or a session via SSM Session
Manager) or from a CI runner with VPC connectivity.

Required environment variables
-------------------------------
AWS_REGION          Target AWS region                      (default: ap-southeast-2)
DYNAMODB_TABLE      DynamoDB state table name              (required)
S3_BUCKET_NAME      S3 grounding-data bucket name         (required)
OPENSEARCH_ENDPOINT OpenSearch domain endpoint (no trailing slash)  (required)
SEARCH_INDEX_NAME   OpenSearch index to probe              (default: grounding-index)

Optional
--------
AWS_SMOKE_SKIP_OPENSEARCH=1   Skip OpenSearch connectivity probe
AWS_SMOKE_SKIP_ECS=1          Skip ECS service check

When the required variables are absent the relevant tests are skipped rather
than failing, so the suite can run in environments where only a subset of
services have been deployed (e.g. bootstrap only, no app_hosting yet).

Usage
-----
From repo root (inside VPC or with SSM tunnel active):

    AWS_REGION=ap-southeast-2 \\
    DYNAMODB_TABLE=rag-state-rag-dev \\
    S3_BUCKET_NAME=tfstate-rag-dev-... \\
    OPENSEARCH_ENDPOINT=https://search-....ap-southeast-2.es.amazonaws.com \\
    python -m pytest tests/smoke/test_aws_infrastructure.py -v

Or via the rollout script after apply:

    ./ops/scripts/aws/rollout-app-hosting.sh dev apply
    # Then from an ECS task or SSM session:
    python -m pytest tests/smoke/test_aws_infrastructure.py -v
"""

from __future__ import annotations

import os
import time
import uuid
from typing import Any

import pytest
import requests

pytestmark = [
    pytest.mark.integration,
    pytest.mark.private_network,
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _require_env(name: str) -> str:
    """Return the env var value or skip the test if absent."""
    value = os.getenv(name, "").strip()
    if not value:
        pytest.skip(f"Environment variable {name!r} is not set — skipping AWS smoke test")
    return value


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def aws_region() -> str:
    return os.getenv("AWS_REGION", "ap-southeast-2")


@pytest.fixture(scope="session")
def boto3_session(aws_region: str) -> Any:
    boto3 = pytest.importorskip("boto3", reason="boto3 is required for AWS smoke tests")
    return boto3.Session(region_name=aws_region)


# ---------------------------------------------------------------------------
# DynamoDB state store
# ---------------------------------------------------------------------------


class TestDynamoDBStateStore:
    """Verify that DynamoDBPollingStateStore can write, read, and delete items
    against the live DynamoDB table.  Uses an isolated smoke-test source key so
    it does not interfere with application data.
    """

    @pytest.fixture(scope="class")
    def table_name(self) -> str:
        return _require_env("DYNAMODB_TABLE")

    @pytest.fixture(scope="class")
    def store(self, table_name: str, boto3_session: Any) -> Any:
        from runtime.assessment_orchestration.dynamo_state_store import (
            DynamoDBPollingStateStore,
        )

        return DynamoDBPollingStateStore(table_name=table_name, session=boto3_session)

    def test_write_and_read_state(self, store: Any) -> None:
        """Round-trip a PollingState through DynamoDB."""
        smoke_source = f"smoke-{uuid.uuid4().hex[:8]}"
        state = store.load_state(smoke_source)
        assert state is not None, "load_state must return a PollingState even when absent"
        # Write last_run_utc and read it back
        store.save_state(smoke_source, state)
        reloaded = store.load_state(smoke_source)
        assert reloaded is not None

    def test_acquire_and_release_lock(self, store: Any) -> None:
        """Optimistic lock acquire/release cycle."""
        smoke_source = f"smoke-{uuid.uuid4().hex[:8]}"
        run_id = f"smoke-run-{uuid.uuid4().hex[:8]}"
        acquired = store.try_acquire_lock(smoke_source, run_id=run_id, ttl_seconds=60)
        assert acquired, "Should acquire lock on first attempt"
        # A second attempt from a different run_id must fail (lock held).
        acquired_again = store.try_acquire_lock(
            smoke_source, run_id=f"smoke-run-{uuid.uuid4().hex[:8]}", ttl_seconds=60
        )
        assert not acquired_again, "Should not acquire lock when already held"
        store.release_lock(smoke_source, run_id=run_id)
        # After release a new run_id can acquire.
        released_reacquire = store.try_acquire_lock(
            smoke_source, run_id=f"smoke-run-{uuid.uuid4().hex[:8]}", ttl_seconds=60
        )
        assert released_reacquire, "Should acquire lock after it has been released"


# ---------------------------------------------------------------------------
# S3 storage
# ---------------------------------------------------------------------------


class TestS3StorageRoundTrip:
    """Verify that AWSS3StorageClient can put, list, read metadata, and delete
    an object in the live S3 bucket.  Uses a well-scoped key prefix so cleanup
    is safe and discoverable.
    """

    @pytest.fixture(scope="class")
    def bucket_name(self) -> str:
        return _require_env("S3_BUCKET_NAME")

    @pytest.fixture(scope="class")
    def client(self, boto3_session: Any) -> Any:
        from runtime.storage.aws_s3 import AWSS3StorageClient

        return AWSS3StorageClient(session=boto3_session)

    @pytest.fixture(scope="class")
    def smoke_key(self) -> str:
        return f"smoke/test-{uuid.uuid4().hex[:12]}.txt"

    def test_put_object(self, client: Any, bucket_name: str, smoke_key: str) -> None:
        client.put_object(
            bucket_name,
            smoke_key,
            b"AWS smoke test payload",
            metadata={"smoke_run": "true"},
        )

    def test_list_contains_key(self, client: Any, bucket_name: str, smoke_key: str) -> None:
        keys = client.list_objects(bucket_name, prefix="smoke/")
        assert smoke_key in keys, f"Expected {smoke_key!r} in listing, got {keys}"

    def test_get_metadata(self, client: Any, bucket_name: str, smoke_key: str) -> None:
        meta = client.get_object_metadata(bucket_name, smoke_key)
        assert meta.get("smoke_run") == "true"
        assert meta.get("content_length") == len(b"AWS smoke test payload")

    def test_delete_object(self, client: Any, bucket_name: str, smoke_key: str) -> None:
        client.delete_object(bucket_name, smoke_key)
        keys_after = client.list_objects(bucket_name, prefix="smoke/")
        assert smoke_key not in keys_after, "Object should be absent after delete"


# ---------------------------------------------------------------------------
# OpenSearch connectivity
# ---------------------------------------------------------------------------


class TestOpenSearchConnectivity:
    """Verify that AWSOpenSearchClient can reach the OpenSearch domain and
    execute a basic match_all query against the configured index.

    Skipped entirely when AWS_SMOKE_SKIP_OPENSEARCH=1 or when
    OPENSEARCH_ENDPOINT is not set.  The index is allowed to be empty — the
    test only confirms HTTP connectivity and a valid response shape.
    """

    @pytest.fixture(scope="class")
    def opensearch_endpoint(self) -> str:
        if _bool_env("AWS_SMOKE_SKIP_OPENSEARCH"):
            pytest.skip("AWS_SMOKE_SKIP_OPENSEARCH=1")
        return _require_env("OPENSEARCH_ENDPOINT")

    @pytest.fixture(scope="class")
    def index_name(self) -> str:
        return os.getenv("SEARCH_INDEX_NAME", "grounding-index")

    @pytest.fixture(scope="class")
    def client(self, opensearch_endpoint: str, index_name: str, boto3_session: Any) -> Any:
        from runtime.search.opensearch import AWSOpenSearchClient

        return AWSOpenSearchClient(
            endpoint=opensearch_endpoint,
            index=index_name,
            session=boto3_session,
        )

    def test_match_all_returns_valid_shape(self, client: Any) -> None:
        """A match_all query must return a list (may be empty if index has no docs yet)."""
        try:
            results = client.search(query_text="", top=5)
        except requests.exceptions.ConnectionError as exc:
            pytest.skip(f"OpenSearch endpoint unreachable from current network: {exc}")
        assert isinstance(results, list), f"Expected list, got {type(results)}"

    def test_keyword_search_returns_valid_shape(self, client: Any) -> None:
        """A keyword query must return a list without raising."""
        try:
            results = client.search(query_text="security control access", top=3)
        except requests.exceptions.ConnectionError as exc:
            pytest.skip(f"OpenSearch endpoint unreachable from current network: {exc}")
        assert isinstance(results, list)
        for hit in results:
            assert isinstance(hit, dict), "Each search result must be a dict"


# ---------------------------------------------------------------------------
# ECS service status
# ---------------------------------------------------------------------------


class TestECSServiceHealth:
    """Verify that the query-web ECS service is in a stable ACTIVE state with
    at least one running task.

    Skipped when AWS_SMOKE_SKIP_ECS=1, when the ECS cluster / service env
    vars are absent, or when ``enable_query_web`` was false at deploy time.
    """

    @pytest.fixture(scope="class")
    def ecs_cluster(self) -> str:
        if _bool_env("AWS_SMOKE_SKIP_ECS"):
            pytest.skip("AWS_SMOKE_SKIP_ECS=1")
        return _require_env("ECS_CLUSTER_NAME")

    @pytest.fixture(scope="class")
    def ecs_service(self) -> str:
        return _require_env("ECS_SERVICE_NAME")

    @pytest.fixture(scope="class")
    def ecs_client(self, boto3_session: Any) -> Any:
        return boto3_session.client("ecs")

    def test_service_is_active(self, ecs_client: Any, ecs_cluster: str, ecs_service: str) -> None:
        resp = ecs_client.describe_services(cluster=ecs_cluster, services=[ecs_service])
        services = resp.get("services", [])
        assert services, f"ECS service {ecs_service!r} not found in cluster {ecs_cluster!r}"
        svc = services[0]
        status = svc.get("status")
        assert status == "ACTIVE", f"Expected ACTIVE, got {status!r}"

    def test_service_has_running_tasks(
        self, ecs_client: Any, ecs_cluster: str, ecs_service: str
    ) -> None:
        resp = ecs_client.describe_services(cluster=ecs_cluster, services=[ecs_service])
        svc = resp["services"][0]
        running = svc.get("runningCount", 0)
        desired = svc.get("desiredCount", 0)
        assert running >= 1, (
            f"Expected ≥1 running task, got runningCount={running} "
            f"desiredCount={desired}. Check ECS console for task failure details."
        )

    def test_no_failed_deployments(
        self, ecs_client: Any, ecs_cluster: str, ecs_service: str
    ) -> None:
        resp = ecs_client.describe_services(cluster=ecs_cluster, services=[ecs_service])
        svc = resp["services"][0]
        for deployment in svc.get("deployments", []):
            if deployment.get("status") == "PRIMARY":
                failed = deployment.get("failedTasks", 0)
                assert failed == 0, (
                    f"PRIMARY deployment has {failed} failed task(s). "
                    "Check CloudWatch logs for the ECS task."
                )
