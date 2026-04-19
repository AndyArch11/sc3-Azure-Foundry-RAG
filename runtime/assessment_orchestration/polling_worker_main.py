from __future__ import annotations

import argparse
import json
import sys

from .polling_worker import (
    _process_assessment_event,
    create_cosmos_state_store_from_env,
    load_poller_config_from_env,
    run_forever,
    run_poll_cycle,
)
from .runtime_wiring import (
    create_confluence_mcp_server_from_env,
    create_orchestrator_adapter_from_env,
)


def main() -> int:
    """Run main."""
    parser = argparse.ArgumentParser(description="Confluence polling worker entrypoint")
    parser.add_argument("--once", action="store_true", help="Run exactly one poll cycle and exit")
    args = parser.parse_args()

    try:
        import logging

        logging.info("[polling_worker_main] Starting poll cycle/main loop")
        config = load_poller_config_from_env()
        state_store = create_cosmos_state_store_from_env()
        server = create_confluence_mcp_server_from_env()
        adapter = create_orchestrator_adapter_from_env()

        def debug_event_handler(event):
            """Run debug event handler."""
            logging.info(
                f"[polling_worker_main] Processing event: {json.dumps(event, sort_keys=True)}"
            )
            triggering_comment_id = str(event.get("content_id") or "")
            logging.info(f"[polling_worker_main] triggering_comment_id: {triggering_comment_id}")
            # Call the normal handler
            return _process_assessment_event(
                adapter=adapter,
                server=server,
                state_store=state_store,
                source=config.source,
                event=event,
                dry_run=config.dry_run,
                assessment_strategy=config.assessment_strategy,
            )

        if args.once:
            result = run_poll_cycle(
                config=config,
                state_store=state_store,
                server=server,
                adapter=adapter,
                process_event=debug_event_handler,
            )
            print(json.dumps({"ok": True, "result": result.__dict__}, sort_keys=True))
            return 0

        run_forever(config, state_store=state_store, server=server, adapter=adapter)
        return 0
    except Exception as exc:  # pragma: no cover - CLI error path
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
