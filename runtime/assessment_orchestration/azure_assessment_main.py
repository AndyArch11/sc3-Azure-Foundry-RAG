from __future__ import annotations

import argparse
import json
import sys

from .azure_assessment import run_azure_assessment


def main(argv: list[str] | None = None) -> int:
    """Run main."""
    parser = argparse.ArgumentParser(description="Azure compliance assessment CLI")
    parser.add_argument("--subscription-id", required=True, help="Azure subscription identifier")
    parser.add_argument("--resource-group", default="", help="Azure resource group name")
    parser.add_argument(
        "--resource-id",
        action="append",
        dest="resource_ids",
        default=[],
        help="Specific Azure resource ID to assess; repeatable",
    )
    parser.add_argument(
        "--controls-framework",
        default="NIST CSF",
        help="Compliance framework to assess against. Azure CLI v1 supports NIST CSF only.",
    )
    args = parser.parse_args(argv)

    try:
        result = run_azure_assessment(
            subscription_id=args.subscription_id,
            resource_group=args.resource_group,
            resource_ids=list(args.resource_ids or []),
            controls_framework=args.controls_framework,
        )
    except Exception as exc:  # pragma: no cover - CLI error path
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 1

    print(json.dumps({"ok": True, "result": result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
