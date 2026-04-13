#!/usr/bin/env python3
"""
Smoke test for Azure resource extraction without Search/OpenAI dependency.

This script validates that the code can successfully extract resources from an Azure
subscription and resource group, without requiring a vector database or LLM framework.

Usage:
    python tests/smoke_test_azure_extraction.py \\
        --subscription-id <subscription-uuid> \\
        --resource-group <rg-name> \\
        [--verbose]

Environment:
    - Must have Azure CLI authenticated (az login)
    - Requires Azure SDK and requests libraries from requirements-dev.txt
"""

import argparse
import json
import sys
from pathlib import Path

# Add workspace root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from runtime.assessment_orchestration.mcp.azure_resource import (
    AzureMCPServer,
    build_azure_target_reference,
)


def validate_azure_extraction(
    subscription_id: str, resource_group: str, verbose: bool = False
) -> dict:
    """
    Validate Azure extraction for a given subscription and resource group.

    Args:
        subscription_id: Azure subscription ID
        resource_group: Azure resource group name
        verbose: Print detailed extraction info

    Returns:
        Dictionary with validation results containing:
        - success: bool indicating if extraction succeeded
        - resource_count: number of resources extracted
        - policy_count: number of policy assignments extracted
        - resource_types: distribution of resource types
        - error: error message if extraction failed
    """
    result = {
        "success": False,
        "subscription_id": subscription_id,
        "resource_group": resource_group,
        "resource_count": 0,
        "policy_count": 0,
        "resource_types": {},
        "error": None,
    }

    try:
        if verbose:
            print(f"\n📋 Smoke Test: Azure Extraction Validation")
            print(f"   Subscription: {subscription_id}")
            print(f"   Resource Group: {resource_group}")
            print()

        # Initialize server with high resource cap for comprehensive extraction
        if verbose:
            print("🔧 Initializing AzureMCPServer...")
        server = AzureMCPServer(max_resources=500)

        # Build and resolve target reference
        if verbose:
            print("🔗 Building target reference...")
        target_reference = build_azure_target_reference(
            subscription_id=subscription_id, resource_group=resource_group
        )

        if verbose:
            print(f"   Target URI: {target_reference}")

        if verbose:
            print("✅ Resolving target...")
        resolved = server.resolve_target(target_reference)

        if verbose:
            print(f"   Resolved Target ID: {resolved.target_id}")

        # Extract content via app-only access (no user context needed)
        if verbose:
            print("📥 Extracting resource details...")
        artifact = server.get_content_by_id(resolved.target_id, identity_mode="app_only")

        # Parse extraction results from artifact.content (JSON)
        extracted_data = json.loads(artifact.content)
        resources = extracted_data.get("resources", [])
        policy_assignments = extracted_data.get("policy_assignments", [])
        policy_definitions = extracted_data.get("policy_definitions", [])

        resource_count = len(resources)
        policy_count = len(policy_assignments)

        result["success"] = True
        result["resource_count"] = resource_count
        result["policy_count"] = policy_count

        # Build resource type distribution
        type_dist = {}
        for resource in resources:
            res_type = resource.get("type", "unknown")
            type_dist[res_type] = type_dist.get(res_type, 0) + 1

        result["resource_types"] = dict(sorted(type_dist.items()))

        if verbose:
            print(f"\n✨ Extraction Results:")
            print(f"   Resources: {resource_count}")
            print(f"   Policy Assignments: {policy_count}")
            print(f"\n🏗️  Resource Type Distribution:")
            for res_type, count in result["resource_types"].items():
                print(f"   {count:2d}× {res_type}")

            if policy_count > 0 and policy_definitions:
                print(f"\n📋 Policy Definitions:")
                for policy_def in policy_definitions:
                    policy_id = policy_def.get("id", "unknown").split("/")[-1]
                    print(f"   - {policy_id}")

    except Exception as e:
        result["error"] = str(e)
        if verbose:
            print(f"\n❌ Extraction failed: {e}")
        return result

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Smoke test for Azure resource extraction",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic test
  python tests/smoke_test_azure_extraction.py \\
    --subscription-id <subscription-id> \
    --resource-group <resource-group>

  # Verbose output
  python tests/smoke_test_azure_extraction.py \
    --subscription-id <subscription-id> \
    --resource-group <resource-group> \
    --verbose

  # JSON output for CI/CD
  python tests/smoke_test_azure_extraction.py \\
    --subscription-id <subscription-id> \
    --resource-group <resource-group> \
    --json
        """,
    )

    parser.add_argument("--subscription-id", required=True, help="Azure subscription ID (UUID)")
    parser.add_argument("--resource-group", required=True, help="Azure resource group name")
    parser.add_argument("--verbose", action="store_true", help="Print detailed extraction info")
    parser.add_argument("--json", action="store_true", help="Output results as JSON")

    args = parser.parse_args()

    result = validate_azure_extraction(
        subscription_id=args.subscription_id,
        resource_group=args.resource_group,
        verbose=args.verbose,
    )

    if args.json:
        print(json.dumps(result, indent=2))
        sys.exit(0 if result["success"] else 1)

    if result["success"]:
        print(f"\n✅ SUCCESS: Extracted {result['resource_count']} resources")
        sys.exit(0)
    else:
        print(f"\n❌ FAILED: {result['error']}")
        sys.exit(1)


if __name__ == "__main__":
    main()
