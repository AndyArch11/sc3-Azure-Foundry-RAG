#!/usr/bin/env python3
"""Diagnostic script to inspect corpus metadata distribution in AWS OpenSearch grounding-index."""

import json
import os
import sys
from typing import Any

try:
    import boto3
    import requests
except ImportError as exc:
    print(f"Error: required modules not installed: {exc}", file=sys.stderr)
    sys.exit(1)


def get_signed_headers(session: Any, method: str, url: str, body: str = "") -> dict[str, str]:
    """Generate AWS SigV4-signed headers for OpenSearch request."""
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest

    credentials = session.get_credentials()
    if credentials is None:
        raise RuntimeError("Unable to resolve AWS credentials")

    frozen_credentials = credentials.get_frozen_credentials()
    request = AWSRequest(
        method=method,
        url=url,
        data=body,
        headers={"Content-Type": "application/json"},
    )
    SigV4Auth(
        frozen_credentials,
        "es",
        session.region_name or os.getenv("AWS_REGION", "us-east-1"),
    ).add_auth(request)
    return dict(request.headers.items())


def main() -> int:
    """Query OpenSearch grounding-index for corpus metadata distribution."""
    endpoint = os.getenv("OPENSEARCH_ENDPOINT", "").strip()
    index_name = os.getenv("OPENSEARCH_GROUNDING_INDEX_NAME", "grounding-index")
    region = os.getenv("AWS_REGION", "ap-southeast-2")

    if not endpoint:
        print("Error: OPENSEARCH_ENDPOINT environment variable not set", file=sys.stderr)
        return 1

    session = boto3.Session(region_name=region)

    # Query 1: Total doc count
    print(f"\n{'='*80}")
    print(f"Diagnostic: {endpoint}/{index_name}")
    print(f"{'='*80}\n")

    search_url = f"{endpoint.rstrip('/')}/{index_name}/_search"

    # Count all documents
    count_body = json.dumps({"size": 0})
    count_headers = get_signed_headers(session, "POST", search_url, count_body)
    count_response = requests.post(search_url, data=count_body, headers=count_headers, timeout=30)
    count_response.raise_for_status()
    total_count = count_response.json().get("hits", {}).get("total", {}).get("value", 0)
    print(f"Total documents in index: {total_count}\n")

    # Query 2: Corpus field distribution
    print("Corpus field value distribution:")
    corpus_agg_body = json.dumps(
        {
            "size": 0,
            "aggs": {
                "corpus_values": {"terms": {"field": "corpus", "size": 100}},
            },
        }
    )
    corpus_headers = get_signed_headers(session, "POST", search_url, corpus_agg_body)
    corpus_response = requests.post(search_url, data=corpus_agg_body, headers=corpus_headers, timeout=30)
    corpus_response.raise_for_status()
    corpus_buckets = (
        corpus_response.json()
        .get("aggregations", {})
        .get("corpus_values", {})
        .get("buckets", [])
    )
    for bucket in corpus_buckets:
        print(f"  '{bucket['key']}': {bucket['doc_count']} docs")

    # Query 3: Corpus_role field distribution
    print("\nCorpus_role field value distribution:")
    role_agg_body = json.dumps(
        {
            "size": 0,
            "aggs": {
                "corpus_role_values": {"terms": {"field": "corpus_role", "size": 100}},
            },
        }
    )
    role_headers = get_signed_headers(session, "POST", search_url, role_agg_body)
    role_response = requests.post(search_url, data=role_agg_body, headers=role_headers, timeout=30)
    role_response.raise_for_status()
    role_buckets = (
        role_response.json()
        .get("aggregations", {})
        .get("corpus_role_values", {})
        .get("buckets", [])
    )
    for bucket in role_buckets:
        print(f"  '{bucket['key']}': {bucket['doc_count']} docs")

    # Query 4: Sample documents
    print("\nSample documents (first 5):")
    sample_body = json.dumps(
        {
            "size": 5,
            "query": {"match_all": {}},
            "_source": ["corpus", "corpus_role", "source_name", "content"],
        }
    )
    sample_headers = get_signed_headers(session, "POST", search_url, sample_body)
    sample_response = requests.post(search_url, data=sample_body, headers=sample_headers, timeout=30)
    sample_response.raise_for_status()
    sample_hits = sample_response.json().get("hits", {}).get("hits", [])
    for i, hit in enumerate(sample_hits, 1):
        source = hit.get("_source", {})
        corpus = source.get("corpus", "(empty)")
        corpus_role = source.get("corpus_role", "(empty)")
        source_name = source.get("source_name", "(no name)")
        content_preview = str(source.get("content", ""))[:100]
        print(f"\n  [{i}] corpus='{corpus}' corpus_role='{corpus_role}'")
        print(f"       source: {source_name}")
        print(f"       preview: {content_preview}...")

    # Query 5: Test the evidence filter
    print("\n" + "="*80)
    print("Testing evidence corpus filter for (b, c, legacy):")
    print("="*80 + "\n")

    filter_body = json.dumps(
        {
            "size": 5,
            "query": {
                "bool": {
                    "must": [{"match_all": {}}],
                    "filter": [
                        {
                            "query_string": {
                                "query": "((corpus:\"b\") OR (corpus_role:\"narrative_guidance\") OR (corpus:\"c\") OR (corpus_role:\"assessed_artifact\") OR (corpus:\"legacy\" OR (NOT _exists_:corpus)))"
                            }
                        }
                    ],
                }
            },
            "_source": ["corpus", "corpus_role", "source_name"],
        }
    )
    filter_headers = get_signed_headers(session, "POST", search_url, filter_body)
    filter_response = requests.post(search_url, data=filter_body, headers=filter_headers, timeout=30)
    filter_response.raise_for_status()
    filter_result = filter_response.json()
    filter_hits = filter_result.get("hits", {})
    filter_total = filter_hits.get("total", {}).get("value", 0)
    print(f"Documents matching B/C/legacy filter: {filter_total}")
    for hit in filter_hits.get("hits", []):
        source = hit.get("_source", {})
        print(f"  - {source.get('source_name', '?')}: corpus='{source.get('corpus')}' role='{source.get('corpus_role')}'")

    print("\n" + "="*80)
    if filter_total == 0:
        print("⚠️  WARNING: No documents matched the B/C/legacy filter!")
        print("This means either:")
        print("  1. No B/C/legacy documents have been ingested yet")
        print("  2. The corpus/corpus_role fields are not indexed")
        print("  3. The filter expression syntax is incorrect for this OpenSearch version")
    else:
        print(f"✓ {filter_total} documents matched the B/C/legacy filter")
    print("="*80 + "\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
