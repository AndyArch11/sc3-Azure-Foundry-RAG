from __future__ import annotations

import argparse
import json

import pytest

from runtime.assessment_orchestration import worker_main


def test_read_message_prefers_message_json() -> None:
    args = argparse.Namespace(message_json='{"k":"v"}', message_file="")
    assert worker_main._read_message(args) == '{"k":"v"}'


def test_read_message_uses_file(tmp_path: pytest.TempPathFactory) -> None:
    msg_file = tmp_path / "msg.json"
    msg_file.write_text('{"a":1}', encoding="utf-8")
    args = argparse.Namespace(message_json="", message_file=str(msg_file))
    assert worker_main._read_message(args) == '{"a":1}'


def test_read_message_uses_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        worker_main.sys, "stdin", type("S", (), {"read": staticmethod(lambda: '{"x":2}')})
    )
    args = argparse.Namespace(message_json="", message_file="")
    assert worker_main._read_message(args) == '{"x":2}'


def test_read_message_raises_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        worker_main.sys, "stdin", type("S", (), {"read": staticmethod(lambda: "   ")})
    )
    args = argparse.Namespace(message_json="", message_file="")
    with pytest.raises(ValueError, match="No queue message provided"):
        worker_main._read_message(args)


def test_worker_main_success(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(worker_main, "create_orchestrator_adapter_from_env", lambda: object())
    monkeypatch.setattr(
        worker_main, "process_queue_message_json", lambda adapter, raw: {"schema_version": "v1.1"}
    )
    monkeypatch.setattr(
        worker_main,
        "_read_message",
        lambda args: '{"queue_message_id":"q1","job":{"job_id":"j1"}}',
    )
    monkeypatch.setattr(worker_main.sys, "argv", ["worker_main.py", "--message-json", "{}"])

    exit_code = worker_main.main()
    captured = capsys.readouterr()
    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["ok"] is True
    assert payload["result"]["schema_version"] == "v1.1"


def test_worker_main_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def _boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(worker_main, "create_orchestrator_adapter_from_env", _boom)
    monkeypatch.setattr(worker_main.sys, "argv", ["worker_main.py", "--message-json", "{}"])

    exit_code = worker_main.main()
    captured = capsys.readouterr()
    assert exit_code == 1
    payload = json.loads(captured.err)
    assert payload["ok"] is False
    assert payload["error"] == "boom"
