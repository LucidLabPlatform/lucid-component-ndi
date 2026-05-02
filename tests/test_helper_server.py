"""Unit tests for PipelineState in helper_server."""
from __future__ import annotations

import logging

import pytest

from lucid_component_ndi.helper_server import PipelineState


class FakePopen:
    """Minimal subprocess.Popen stand-in. `_alive=True` means poll() returns None."""

    _next_pid = 1000

    def __init__(self, cmd, env=None, **_kwargs):
        self.cmd = cmd
        self.env = env
        FakePopen._next_pid += 1
        self.pid = FakePopen._next_pid
        self._alive = True

    def poll(self):
        return None if self._alive else 0

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self._alive = False


@pytest.fixture
def fake_popen(monkeypatch):
    """Patch subprocess.Popen in helper_server to return FakePopen instances."""
    instances: list[FakePopen] = []

    def factory(cmd, env=None, **kwargs):
        p = FakePopen(cmd, env=env, **kwargs)
        instances.append(p)
        return p

    monkeypatch.setattr(
        "lucid_component_ndi.helper_server.subprocess.Popen", factory
    )
    return instances


def test_start_receive_when_running_restarts(fake_popen, monkeypatch, caplog):
    """A second start_receive while a receive is running should stop the old
    process, log a warning, and start a new process — returning ok=True."""
    state = PipelineState()

    r1 = state.start_receive(["yuri_simple", "--rx"], {"FOO": "1"})
    assert r1 == {"ok": True, "pid": fake_popen[0].pid}

    def kill_first(pid, _sig):
        assert pid == fake_popen[0].pid
        fake_popen[0]._alive = False

    monkeypatch.setattr(
        "lucid_component_ndi.helper_server.os.kill", kill_first
    )

    caplog.set_level(logging.WARNING, logger="lucid.ndi.helper")

    r2 = state.start_receive(["yuri_simple", "--rx"], {"FOO": "2"})

    assert r2["ok"] is True
    assert r2["pid"] == fake_popen[1].pid
    assert any(
        "receive pipeline already running" in rec.message
        and rec.levelname == "WARNING"
        for rec in caplog.records
    ), f"expected warning log, got: {[r.message for r in caplog.records]}"
    assert fake_popen[0]._alive is False


def test_start_send_when_running_restarts(fake_popen, monkeypatch, caplog):
    """A second start_send while a send is running should stop the old
    process, log a warning, and start a new process — returning ok=True."""
    state = PipelineState()

    r1 = state.start_send(["yuri_simple", "--tx"], {"FOO": "1"})
    assert r1 == {"ok": True, "pid": fake_popen[0].pid}

    def kill_first(pid, _sig):
        assert pid == fake_popen[0].pid
        fake_popen[0]._alive = False

    monkeypatch.setattr(
        "lucid_component_ndi.helper_server.os.kill", kill_first
    )

    caplog.set_level(logging.WARNING, logger="lucid.ndi.helper")

    r2 = state.start_send(["yuri_simple", "--tx"], {"FOO": "2"})

    assert r2["ok"] is True
    assert r2["pid"] == fake_popen[1].pid
    assert any(
        "send pipeline already running" in rec.message
        and rec.levelname == "WARNING"
        for rec in caplog.records
    ), f"expected warning log, got: {[r.message for r in caplog.records]}"
    assert fake_popen[0]._alive is False
