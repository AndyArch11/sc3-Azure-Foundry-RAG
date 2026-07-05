"""
Confluence polling worker main module.

This module serves as the entry point for the Confluence polling worker CLI.
It handles command-line argument parsing, logging configuration, and orchestrates the execution of the polling process by invoking the run_forever or run_poll_cycle functions from the polling_worker module.

"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from runtime.log_config import configure_logging as _configure_logging

_configure_logging("polling-worker-main")

_LOGGER = logging.getLogger(__name__)

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
    """Run main.

    Returns:
        Exit code: 0 on success, 1 on error.
    """
    parser = argparse.ArgumentParser(description="Confluence polling worker entrypoint")
    parser.add_argument("--once", action="store_true", help="Run exactly one poll cycle and exit")
    args = parser.parse_args()

    try:
        _LOGGER.info("Starting poll cycle/main loop", extra={"event": "worker_start"})
        config = load_poller_config_from_env()
        state_store = create_cosmos_state_store_from_env()
        server = create_confluence_mcp_server_from_env()
        adapter = create_orchestrator_adapter_from_env()

        def debug_event_handler(event):
            """Run debug event handler."""
            triggering_comment_id = str(event.get("content_id") or "")
            _LOGGER.info(
                "Processing assessment event",
                extra={
                    "event": "poll_event_received",
                    "content_id": triggering_comment_id,
                },
            )
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
