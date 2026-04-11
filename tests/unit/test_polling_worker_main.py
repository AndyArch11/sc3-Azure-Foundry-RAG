from __future__ import annotations

import json

import pytest

from runtime.assessment_orchestration import polling_worker_main
from runtime.assessment_orchestration.polling_worker import PollCycleResult


def test_polling_worker_main_once_mode_success(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(polling_worker_main, "load_poller_config_from_env", lambda: object())
    monkeypatch.setattr(polling_worker_main, "create_cosmos_state_store_from_env", lambda: object())
    monkeypatch.setattr(
        polling_worker_main, "create_confluence_mcp_server_from_env", lambda: object()
    )
    monkeypatch.setattr(
        polling_worker_main, "create_orchestrator_adapter_from_env", lambda: object()
    )
    monkeypatch.setattr(
        polling_worker_main,
        "run_poll_cycle",
        lambda **kwargs: PollCycleResult(
            acquired_lease=True,
            fetched_events=2,
            processed_events=2,
            terminal_failures=0,
            watermark="2026-01-01T00:00:00+00:00",
        ),
    )

    exit_code = (
        polling_worker_main.main.__wrapped__()
        if hasattr(polling_worker_main.main, "__wrapped__")
        else None
    )
    # Fallback call path for regular function
    if exit_code is None:
        monkeypatch.setattr(__import__("sys"), "argv", ["polling_worker_main.py", "--once"])
        exit_code = polling_worker_main.main()

    captured = capsys.readouterr()
    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["ok"] is True
    assert payload["result"]["processed_events"] == 2


def test_polling_worker_main_forever_mode_calls_run_forever(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = {"forever": False}

    monkeypatch.setattr(polling_worker_main, "load_poller_config_from_env", lambda: object())
    monkeypatch.setattr(polling_worker_main, "create_cosmos_state_store_from_env", lambda: object())
    monkeypatch.setattr(
        polling_worker_main, "create_confluence_mcp_server_from_env", lambda: object()
    )
    monkeypatch.setattr(
        polling_worker_main, "create_orchestrator_adapter_from_env", lambda: object()
    )

    def _run_forever(*args, **kwargs):
        called["forever"] = True

    monkeypatch.setattr(polling_worker_main, "run_forever", _run_forever)
    monkeypatch.setattr(__import__("sys"), "argv", ["polling_worker_main.py"])

    exit_code = polling_worker_main.main()
    assert exit_code == 0
    assert called["forever"] is True


def test_polling_worker_main_returns_nonzero_on_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def _boom():
        raise RuntimeError("broken")

    monkeypatch.setattr(polling_worker_main, "load_poller_config_from_env", _boom)
    monkeypatch.setattr(__import__("sys"), "argv", ["polling_worker_main.py", "--once"])

    exit_code = polling_worker_main.main()
    captured = capsys.readouterr()
    assert exit_code == 1
    payload = json.loads(captured.err)
    assert payload["ok"] is False
    assert payload["error"] == "broken"
