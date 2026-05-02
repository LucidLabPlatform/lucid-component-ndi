# Restart-on-busy for NDI start commands — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `PipelineState.start_receive` and `PipelineState.start_send` in the NDI helper daemon treat duplicate starts as implicit restarts (log + stop + start) instead of returning an error.

**Architecture:** Refactor `_stop` into a thin lock-acquiring wrapper around an unlocked `_stop_locked(pipeline)`. In `start_receive` / `start_send`, when the relevant process is alive, log a WARNING and call `_stop_locked` before falling through to the existing spawn path. All under the start method's existing `self._lock`.

**Tech Stack:** Python 3.11, `subprocess`, `threading`, `pytest`, `unittest.mock`.

---

## File Structure

- **Modify:** `src/lucid_component_ndi/helper_server.py` — refactor `_stop`, change `start_receive` and `start_send`.
- **Create:** `tests/test_helper_server.py` — unit tests for `PipelineState.start_receive` / `start_send` restart-on-busy behavior. (Existing `tests/test_contract.py` mocks the helper client, so it does not exercise `PipelineState` directly.)

---

### Task 1: Refactor `_stop` into `_stop_locked`

**Files:**
- Modify: `src/lucid_component_ndi/helper_server.py:53-116`

This is a pure refactor with no behavior change. We extract the body of `_stop` into a new `_stop_locked` method that assumes the caller holds `self._lock`. `_stop` becomes a thin wrapper. This is needed so that `start_receive` / `start_send` (which already hold the lock) can perform a stop without re-entering the non-reentrant `threading.Lock`.

- [ ] **Step 1: Make the change**

Replace the current `stop_receive`, `stop_send`, and `_stop` methods (lines 53-55, 73-75, 95-116) with:

```python
    def stop_receive(self) -> dict:
        with self._lock:
            return self._stop_locked("receive")

    def stop_send(self) -> dict:
        with self._lock:
            return self._stop_locked("send")

    def _stop_locked(self, pipeline: str) -> dict:
        """Stop a pipeline. Caller MUST hold self._lock."""
        proc = self._receive_proc if pipeline == "receive" else self._send_proc
        if not proc or proc.poll() is not None:
            if pipeline == "receive":
                self._receive_proc = None
            else:
                self._send_proc = None
            return {"ok": True}
        try:
            os.kill(proc.pid, signal.SIGTERM)
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3.0)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        if pipeline == "receive":
            self._receive_proc = None
        else:
            self._send_proc = None
        logger.info("%s pipeline stopped", pipeline)
        return {"ok": True}
```

Delete the old `_stop` method entirely (no other callers remain).

- [ ] **Step 2: Verify existing tests still pass**

Run: `cd /Users/farahorfaly/Desktop/LUCID/components/lucid-component-ndi && pytest tests/ -q`
Expected: all existing tests pass (refactor is behavior-preserving).

- [ ] **Step 3: Commit**

```bash
cd /Users/farahorfaly/Desktop/LUCID/components/lucid-component-ndi
git add src/lucid_component_ndi/helper_server.py
git commit -m "refactor(ndi-helper): extract _stop_locked for re-entrant use"
```

---

### Task 2: Add failing test for restart-on-busy in `start_receive`

**Files:**
- Create: `tests/test_helper_server.py`

We test `PipelineState.start_receive` directly with a fake `subprocess.Popen` so the test is hermetic (no real processes spawned).

- [ ] **Step 1: Write the failing test**

Create `tests/test_helper_server.py`:

```python
"""Unit tests for PipelineState in helper_server."""
from __future__ import annotations

import logging
from unittest.mock import patch

import pytest

from lucid_component_ndi.helper_server import PipelineState


class FakePopen:
    """Minimal subprocess.Popen stand-in. `alive=True` means poll() returns None."""

    _next_pid = 1000

    def __init__(self, cmd, env=None, **_kwargs):
        self.cmd = cmd
        self.env = env
        FakePopen._next_pid += 1
        self.pid = FakePopen._next_pid
        self._alive = True
        self._wait_called = False

    def poll(self):
        return None if self._alive else 0

    def wait(self, timeout=None):
        self._wait_called = True
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


@pytest.fixture
def fake_kill(monkeypatch):
    """Mark FakePopen as dead when os.kill SIGTERM is sent."""
    def _kill(pid, _sig):
        # Find the live FakePopen with this pid via the module's PipelineState refs.
        # The test will set this side effect via monkeypatch on a per-test basis.
        pass
    monkeypatch.setattr("lucid_component_ndi.helper_server.os.kill", _kill)


def test_start_receive_when_running_restarts(fake_popen, monkeypatch, caplog):
    """A second start_receive while a receive is running should stop the old
    process, log a warning, and start a new process — returning ok=True."""
    state = PipelineState()

    # First start: spawns FakePopen #1.
    r1 = state.start_receive(["yuri_simple", "--rx"], {"FOO": "1"})
    assert r1 == {"ok": True, "pid": fake_popen[0].pid}

    # When os.kill is invoked during restart, mark the first process dead so
    # _stop_locked's wait() returns and we proceed to spawn #2.
    def kill_first(pid, _sig):
        assert pid == fake_popen[0].pid
        fake_popen[0]._alive = False

    monkeypatch.setattr(
        "lucid_component_ndi.helper_server.os.kill", kill_first
    )

    caplog.set_level(logging.WARNING, logger="lucid.ndi.helper")

    # Second start: should restart, not fail.
    r2 = state.start_receive(["yuri_simple", "--rx"], {"FOO": "2"})

    assert r2["ok"] is True
    assert r2["pid"] == fake_popen[1].pid
    assert any(
        "receive pipeline already running" in rec.message
        and rec.levelname == "WARNING"
        for rec in caplog.records
    ), f"expected warning log, got: {[r.message for r in caplog.records]}"
    # Old process was terminated.
    assert fake_popen[0]._alive is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/farahorfaly/Desktop/LUCID/components/lucid-component-ndi && pytest tests/test_helper_server.py::test_start_receive_when_running_restarts -v`
Expected: FAIL — current code returns `{"ok": False, "error": "receive pipeline already running"}`, so the `r2["ok"] is True` assertion fails.

---

### Task 3: Implement restart-on-busy for `start_receive`

**Files:**
- Modify: `src/lucid_component_ndi/helper_server.py:37-51`

- [ ] **Step 1: Update `start_receive`**

Replace the existing `start_receive` method with:

```python
    def start_receive(self, cmd: list[str], env: dict[str, str]) -> dict:
        with self._lock:
            if self._receive_proc and self._receive_proc.poll() is None:
                logger.warning(
                    "receive pipeline already running (PID %d), restarting",
                    self._receive_proc.pid,
                )
                stop_res = self._stop_locked("receive")
                if not stop_res.get("ok", False):
                    return stop_res
            try:
                merged_env = {**os.environ, **env}
                proc = subprocess.Popen(
                    cmd, env=merged_env, stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE, start_new_session=True,
                )
                self._receive_proc = proc
                logger.info("Receive started (PID %d): %s", proc.pid, " ".join(cmd))
                return {"ok": True, "pid": proc.pid}
            except Exception as exc:
                return {"ok": False, "error": str(exc)}
```

- [ ] **Step 2: Run test to verify it passes**

Run: `cd /Users/farahorfaly/Desktop/LUCID/components/lucid-component-ndi && pytest tests/test_helper_server.py::test_start_receive_when_running_restarts -v`
Expected: PASS.

- [ ] **Step 3: Run full test suite**

Run: `cd /Users/farahorfaly/Desktop/LUCID/components/lucid-component-ndi && pytest tests/ -q`
Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
cd /Users/farahorfaly/Desktop/LUCID/components/lucid-component-ndi
git add src/lucid_component_ndi/helper_server.py tests/test_helper_server.py
git commit -m "feat(ndi-helper): restart receive pipeline on duplicate start_receive"
```

---

### Task 4: Add failing test for restart-on-busy in `start_send`

**Files:**
- Modify: `tests/test_helper_server.py`

- [ ] **Step 1: Append the failing test**

Append to `tests/test_helper_server.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/farahorfaly/Desktop/LUCID/components/lucid-component-ndi && pytest tests/test_helper_server.py::test_start_send_when_running_restarts -v`
Expected: FAIL — current `start_send` returns `{"ok": False, "error": "send pipeline already running"}`.

---

### Task 5: Implement restart-on-busy for `start_send`

**Files:**
- Modify: `src/lucid_component_ndi/helper_server.py:57-71`

- [ ] **Step 1: Update `start_send`**

Replace the existing `start_send` method with:

```python
    def start_send(self, cmd: list[str], env: dict[str, str]) -> dict:
        with self._lock:
            if self._send_proc and self._send_proc.poll() is None:
                logger.warning(
                    "send pipeline already running (PID %d), restarting",
                    self._send_proc.pid,
                )
                stop_res = self._stop_locked("send")
                if not stop_res.get("ok", False):
                    return stop_res
            try:
                merged_env = {**os.environ, **env}
                proc = subprocess.Popen(
                    cmd, env=merged_env, stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE, start_new_session=True,
                )
                self._send_proc = proc
                logger.info("Send started (PID %d): %s", proc.pid, " ".join(cmd))
                return {"ok": True, "pid": proc.pid}
            except Exception as exc:
                return {"ok": False, "error": str(exc)}
```

- [ ] **Step 2: Run send test to verify it passes**

Run: `cd /Users/farahorfaly/Desktop/LUCID/components/lucid-component-ndi && pytest tests/test_helper_server.py::test_start_send_when_running_restarts -v`
Expected: PASS.

- [ ] **Step 3: Run full test suite**

Run: `cd /Users/farahorfaly/Desktop/LUCID/components/lucid-component-ndi && pytest tests/ -q`
Expected: all tests pass.

- [ ] **Step 4: Lint**

Run: `cd /Users/farahorfaly/Desktop/LUCID/components/lucid-component-ndi && ruff check src/lucid_component_ndi/helper_server.py tests/test_helper_server.py`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
cd /Users/farahorfaly/Desktop/LUCID/components/lucid-component-ndi
git add src/lucid_component_ndi/helper_server.py tests/test_helper_server.py
git commit -m "feat(ndi-helper): restart send pipeline on duplicate start_send"
```
