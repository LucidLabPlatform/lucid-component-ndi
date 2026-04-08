# NDI Component Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the template `ExampleComponent` in `lucid-component-ndi` with a fully functional `NDIComponent` that controls libyuri NDI send/receive pipelines via MQTT commands on Raspberry Pi.

**Architecture:** The component wraps the `yuri_simple` binary as subprocesses — one for the NDI sender (V4L2 → NDI broadcast) and one for the NDI receiver (NDI stream → GLX display). A background monitor thread checks process liveness every 5 seconds and publishes telemetry. All command/state/config follows the LUCID component contract from `lucid-component-base`.

**Tech Stack:** Python 3.11+, `subprocess`, `threading`, `lucid-component-base`, `yuri_simple` binary (libyuri), `pytest`

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `src/lucid_component_ndi/__init__.py` | Create | Package export — exports `NDIComponent` |
| `src/lucid_component_ndi/component.py` | Create | Full `NDIComponent` implementation |
| `src/lucid_component_example/__init__.py` | Delete | Template — replaced |
| `src/lucid_component_example/component.py` | Delete | Template — replaced |
| `tests/test_contract.py` | Rewrite | Contract + command tests for `NDIComponent` |
| `pyproject.toml` | Modify | Package name, entry point, package includes |

---

## Task 1: Update pyproject.toml

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Edit pyproject.toml**

Replace the name, description, entry point, and package include list:

```toml
[build-system]
requires = ["setuptools>=69", "setuptools_scm[toml]>=8", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "lucid-component-ndi"
dynamic = ["version"]
description = "LUCID component for NDI send/receive via libyuri on Raspberry Pi."
readme = "README.md"
requires-python = ">=3.11"
license = {text = "MIT"}
authors = [{ name = "LUCID" }]
dependencies = [
  "lucid-component-base @ git+https://github.com/LucidLabPlatform/lucid-component-base@v1.1.2",
]

[project.optional-dependencies]
dev = ["pytest"]

[project.entry-points."lucid.components"]
ndi = "lucid_component_ndi.component:NDIComponent"

[tool.setuptools]
package-dir = {"" = "src"}

[tool.setuptools.packages.find]
where = ["src"]
include = ["lucid_component_ndi*"]

[tool.setuptools_scm]
version_scheme = "no-guess-dev"
local_scheme = "no-local-version"
fallback_version = "0.0.0"

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
```

- [ ] **Step 2: Verify no syntax errors**

```bash
cd /Users/farahorfaly/Desktop/LUCID/lucid-component-ndi
python -c "import tomllib; tomllib.loads(open('pyproject.toml').read()); print('OK')"
```

Expected: `OK`

---

## Task 2: Create the `lucid_component_ndi` package

**Files:**
- Create: `src/lucid_component_ndi/__init__.py`
- Delete: `src/lucid_component_example/__init__.py`
- Delete: `src/lucid_component_example/component.py`

- [ ] **Step 1: Create the package init**

Create `src/lucid_component_ndi/__init__.py`:

```python
from lucid_component_ndi.component import NDIComponent

__all__ = ["NDIComponent"]
```

- [ ] **Step 2: Remove the template package**

```bash
rm -rf /Users/farahorfaly/Desktop/LUCID/lucid-component-ndi/src/lucid_component_example
```

---

## Task 3: Write failing contract tests first (TDD)

**Files:**
- Rewrite: `tests/test_contract.py`

- [ ] **Step 1: Write the full test file**

Replace `tests/test_contract.py` with:

```python
"""
Contract tests for NDIComponent.
Tests run without hardware — subprocess.Popen is mocked throughout.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from lucid_component_base import ComponentContext, ComponentStatus

# NDIComponent not yet created — tests will fail until Task 4 is done
from lucid_component_ndi import NDIComponent


def _fake_context(config: dict | None = None) -> ComponentContext:
    class FakeMqtt:
        def __init__(self):
            self.published: list[tuple] = []

        def publish(self, topic: str, payload, *, qos: int = 0, retain: bool = False) -> None:
            self.published.append((topic, payload))

    return ComponentContext.create(
        agent_id="test-agent",
        base_topic="lucid/agents/test-agent",
        component_id="ndi",
        mqtt=FakeMqtt(),
        config=config or {},
    )


def _make_comp(config: dict | None = None) -> NDIComponent:
    return NDIComponent(_fake_context(config))


# ── Instantiation ─────────────────────────────────────────────────────────────

def test_component_instantiation():
    comp = _make_comp()
    assert comp.component_id == "ndi"
    assert comp.state.status == ComponentStatus.STOPPED


def test_get_state_payload_structure():
    comp = _make_comp()
    state = comp.get_state_payload()
    assert isinstance(state, dict)
    for key in ("send_active", "send_pid", "send_stream_name",
                "receive_active", "receive_pid", "receive_stream_name"):
        assert key in state, f"Missing key: {key}"
    assert state["send_active"] is False
    assert state["receive_active"] is False


def test_get_cfg_payload_structure():
    comp = _make_comp()
    cfg = comp.get_cfg_payload()
    assert isinstance(cfg, dict)
    for key in ("ndi_path", "yuri_binary", "xdg_runtime_dir",
                "cpu_cores", "nice_level", "send_device",
                "send_stream_name", "receive_stream_name", "receive_fullscreen"):
        assert key in cfg, f"Missing cfg key: {key}"


def test_capabilities_includes_all_commands():
    comp = _make_comp()
    caps = comp.capabilities()
    assert isinstance(caps, list)
    for cmd in ("reset", "ping", "send/start", "send/stop",
                "receive/start", "receive/stop"):
        assert cmd in caps, f"Missing capability: {cmd}"


# ── Lifecycle ─────────────────────────────────────────────────────────────────

def test_start_stop_state_transitions():
    comp = _make_comp()
    comp.start()
    assert comp.state.status == ComponentStatus.RUNNING
    assert comp.state.started_at is not None
    comp.stop()
    assert comp.state.status == ComponentStatus.STOPPED
    assert comp.state.stopped_at is not None


def test_start_idempotent():
    comp = _make_comp()
    comp.start()
    comp.start()  # should not raise
    comp.stop()


def test_stop_idempotent():
    comp = _make_comp()
    comp.stop()  # should not raise when already stopped


# ── cmd/ping ──────────────────────────────────────────────────────────────────

def test_on_cmd_ping_publishes_result():
    comp = _make_comp()
    mqtt = comp.context.mqtt
    comp.on_cmd_ping(json.dumps({"request_id": "ping-001"}))
    result_topics = [t for t, _ in mqtt.published if "evt/ping/result" in t]
    assert result_topics, "No evt/ping/result published"
    payload = json.loads(mqtt.published[-1][1])
    assert payload["ok"] is True
    assert payload["request_id"] == "ping-001"


# ── cmd/cfg/set ───────────────────────────────────────────────────────────────

def test_on_cmd_cfg_set_updates_send_stream_name():
    comp = _make_comp()
    comp.on_cmd_cfg_set(json.dumps({
        "request_id": "cfg-001",
        "set": {"send_stream_name": "my-camera"},
    }))
    state = comp.get_state_payload()
    assert state["send_stream_name"] == "my-camera"


def test_on_cmd_cfg_set_unknown_key_is_ignored():
    comp = _make_comp()
    comp.on_cmd_cfg_set(json.dumps({
        "request_id": "cfg-002",
        "set": {"unknown_key": "value"},
    }))
    mqtt = comp.context.mqtt
    result_msgs = [json.loads(p) for t, p in mqtt.published if "evt/cfg/set/result" in t]
    assert result_msgs, "No cfg/set result published"
    assert result_msgs[-1]["ok"] is True  # unknown keys silently ignored


def test_on_cmd_cfg_set_invalid_json_does_not_crash():
    comp = _make_comp()
    comp.on_cmd_cfg_set("not json")  # should not raise


# ── cmd/send/start ────────────────────────────────────────────────────────────

def test_on_cmd_send_start_spawns_process():
    comp = _make_comp()
    comp.start()
    mock_proc = MagicMock()
    mock_proc.pid = 12345
    mock_proc.poll.return_value = None  # process alive
    with patch("subprocess.Popen", return_value=mock_proc) as mock_popen:
        comp.on_cmd_send_start(json.dumps({"request_id": "send-001"}))
        assert mock_popen.called
    state = comp.get_state_payload()
    assert state["send_active"] is True
    assert state["send_pid"] == 12345
    comp.stop()


def test_on_cmd_send_start_already_running_returns_error():
    comp = _make_comp()
    comp.start()
    mock_proc = MagicMock()
    mock_proc.pid = 999
    mock_proc.poll.return_value = None
    with patch("subprocess.Popen", return_value=mock_proc):
        comp.on_cmd_send_start(json.dumps({"request_id": "send-001"}))
        comp.on_cmd_send_start(json.dumps({"request_id": "send-002"}))
    mqtt = comp.context.mqtt
    results = [json.loads(p) for t, p in mqtt.published if "evt/send/start/result" in t]
    assert results[-1]["ok"] is False
    assert "already" in results[-1]["error"].lower()
    comp.stop()


# ── cmd/send/stop ─────────────────────────────────────────────────────────────

def test_on_cmd_send_stop_terminates_process():
    comp = _make_comp()
    comp.start()
    mock_proc = MagicMock()
    mock_proc.pid = 12345
    mock_proc.poll.return_value = None
    with patch("subprocess.Popen", return_value=mock_proc):
        comp.on_cmd_send_start(json.dumps({"request_id": "send-001"}))
    mock_proc.poll.return_value = 0  # process exited after terminate
    comp.on_cmd_send_stop(json.dumps({"request_id": "stop-001"}))
    assert mock_proc.terminate.called
    state = comp.get_state_payload()
    assert state["send_active"] is False
    comp.stop()


def test_on_cmd_send_stop_when_not_running():
    comp = _make_comp()
    comp.start()
    comp.on_cmd_send_stop(json.dumps({"request_id": "stop-001"}))
    mqtt = comp.context.mqtt
    results = [json.loads(p) for t, p in mqtt.published if "evt/send/stop/result" in t]
    assert results[-1]["ok"] is True  # idempotent — not an error


# ── cmd/receive/start ─────────────────────────────────────────────────────────

def test_on_cmd_receive_start_requires_stream_name():
    comp = _make_comp()  # receive_stream_name defaults to ""
    comp.start()
    comp.on_cmd_receive_start(json.dumps({"request_id": "recv-001"}))
    mqtt = comp.context.mqtt
    results = [json.loads(p) for t, p in mqtt.published if "evt/receive/start/result" in t]
    assert results[-1]["ok"] is False
    assert "stream" in results[-1]["error"].lower()
    comp.stop()


def test_on_cmd_receive_start_spawns_process():
    comp = _make_comp({"receive_stream_name": "CAMERA (test)"})
    comp.start()
    mock_proc = MagicMock()
    mock_proc.pid = 54321
    mock_proc.poll.return_value = None
    with patch("subprocess.Popen", return_value=mock_proc):
        comp.on_cmd_receive_start(json.dumps({"request_id": "recv-001"}))
    state = comp.get_state_payload()
    assert state["receive_active"] is True
    assert state["receive_pid"] == 54321
    comp.stop()


# ── cmd/reset ─────────────────────────────────────────────────────────────────

def test_on_cmd_reset_stops_both_processes():
    comp = _make_comp({"receive_stream_name": "CAMERA (test)"})
    comp.start()
    mock_send = MagicMock()
    mock_send.pid = 111
    mock_send.poll.return_value = None
    mock_recv = MagicMock()
    mock_recv.pid = 222
    mock_recv.poll.return_value = None
    with patch("subprocess.Popen", side_effect=[mock_send, mock_recv]):
        comp.on_cmd_send_start(json.dumps({"request_id": "s1"}))
        comp.on_cmd_receive_start(json.dumps({"request_id": "r1"}))
    mock_send.poll.return_value = 0
    mock_recv.poll.return_value = 0
    comp.on_cmd_reset(json.dumps({"request_id": "reset-001"}))
    state = comp.get_state_payload()
    assert state["send_active"] is False
    assert state["receive_active"] is False
    comp.stop()
```

- [ ] **Step 2: Run tests to confirm they all fail (import error expected)**

```bash
cd /Users/farahorfaly/Desktop/LUCID/lucid-component-ndi
python -m pytest tests/test_contract.py -v 2>&1 | head -30
```

Expected: `ModuleNotFoundError: No module named 'lucid_component_ndi'` — that's correct, implementation doesn't exist yet.

---

## Task 4: Implement `NDIComponent`

**Files:**
- Create: `src/lucid_component_ndi/component.py`

- [ ] **Step 1: Create the implementation**

Create `src/lucid_component_ndi/component.py`:

```python
"""
LUCID NDI component — controls libyuri NDI send/receive pipelines via subprocess.

Send pipeline (V4L2 → NDI broadcast):
  yuri_simple v4l2_source[device="{device}"] ndi_output[stream="{name}"][ndi_path="{path}"]

Receive pipeline (NDI → GLX display):
  yuri_simple ndi_input[stream="{name}"][ndi_path="{path}"] frame_info[] glx_window[fullscreen=true]

Both pipelines are optionally wrapped with `taskset -c {cores}` and `nice -n {level}`.
Environment: XDG_RUNTIME_DIR and NDI_PATH are set for each subprocess.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from datetime import datetime, timezone
from typing import Any

from lucid_component_base import Component, ComponentContext


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# Config keys and their defaults
_CFG_DEFAULTS: dict[str, Any] = {
    "ndi_path": "/usr/local/lib/libndi.so",
    "yuri_binary": "yuri_simple",
    "xdg_runtime_dir": "/run/user/1000",
    "cpu_cores": "1-3",
    "nice_level": "5",
    "send_device": "/dev/video0",
    "send_stream_name": "lucid-ndi",
    "receive_stream_name": "",
    "receive_fullscreen": True,
}

_ALLOWED_CFG_KEYS = set(_CFG_DEFAULTS.keys())

_MONITOR_INTERVAL_S = 5.0
_TERMINATE_WAIT_S = 3.0


class NDIComponent(Component):
    """
    Controls libyuri NDI send/receive subprocesses.

    Commands: reset, ping, cfg/set, send/start, send/stop, receive/start, receive/stop
    Telemetry: send_running (bool), receive_running (bool) — every 5 s
    """

    def __init__(self, context: ComponentContext) -> None:
        super().__init__(context)
        self._log = context.logger()

        # Merge context.config into defaults
        self._cfg: dict[str, Any] = {**_CFG_DEFAULTS, **context.config}

        # Runtime process state
        self._send_proc: subprocess.Popen | None = None
        self._recv_proc: subprocess.Popen | None = None

        # Monitor thread
        self._monitor_stop = threading.Event()
        self._monitor_thread: threading.Thread | None = None

    # ── Identity ──────────────────────────────────────────────────────────────

    @property
    def component_id(self) -> str:
        return "ndi"

    def capabilities(self) -> list[str]:
        return ["reset", "ping", "send/start", "send/stop", "receive/start", "receive/stop"]

    # ── State / Config ────────────────────────────────────────────────────────

    def get_state_payload(self) -> dict[str, Any]:
        return {
            "send_active": self._send_proc is not None and self._send_proc.poll() is None,
            "send_pid": self._send_proc.pid if self._send_proc is not None else None,
            "send_stream_name": self._cfg["send_stream_name"],
            "receive_active": self._recv_proc is not None and self._recv_proc.poll() is None,
            "receive_pid": self._recv_proc.pid if self._recv_proc is not None else None,
            "receive_stream_name": self._cfg["receive_stream_name"],
        }

    def get_cfg_payload(self) -> dict[str, Any]:
        return dict(self._cfg)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def _start(self) -> None:
        self.set_telemetry_config({
            "send_running": {"enabled": True, "interval_s": _MONITOR_INTERVAL_S, "change_threshold_percent": 0},
            "receive_running": {"enabled": True, "interval_s": _MONITOR_INTERVAL_S, "change_threshold_percent": 0},
        })
        self._publish_all_retained()
        self._monitor_stop.clear()
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop, daemon=True, name="ndi-monitor"
        )
        self._monitor_thread.start()
        self._log.info("NDI component started")

    def _stop(self) -> None:
        self._monitor_stop.set()
        if self._monitor_thread is not None:
            self._monitor_thread.join(timeout=_MONITOR_INTERVAL_S + 1)
            self._monitor_thread = None
        self._terminate_process("send")
        self._terminate_process("receive")
        self._log.info("NDI component stopped")

    # ── Monitor Loop ──────────────────────────────────────────────────────────

    def _monitor_loop(self) -> None:
        while not self._monitor_stop.wait(timeout=_MONITOR_INTERVAL_S):
            self._check_process("send")
            self._check_process("receive")
            send_alive = self._send_proc is not None and self._send_proc.poll() is None
            recv_alive = self._recv_proc is not None and self._recv_proc.poll() is None
            self.publish_telemetry("send_running", send_alive)
            self.publish_telemetry("receive_running", recv_alive)

    def _check_process(self, name: str) -> None:
        """If a tracked process has exited unexpectedly, clear its slot and log."""
        proc = self._send_proc if name == "send" else self._recv_proc
        if proc is None:
            return
        rc = proc.poll()
        if rc is not None:
            self._log.warning("NDI %s process (pid=%d) exited unexpectedly (rc=%d)", name, proc.pid, rc)
            if name == "send":
                self._send_proc = None
            else:
                self._recv_proc = None
            self.publish_state()

    # ── Pipeline Helpers ──────────────────────────────────────────────────────

    def _build_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["XDG_RUNTIME_DIR"] = self._cfg["xdg_runtime_dir"]
        env["NDI_PATH"] = self._cfg["ndi_path"]
        return env

    def _build_cmd_prefix(self) -> list[str]:
        """Return [taskset, -c, cores, nice, -n, level] prefix, or empty list."""
        prefix: list[str] = []
        cores = str(self._cfg.get("cpu_cores", "")).strip()
        nice = str(self._cfg.get("nice_level", "")).strip()
        if cores:
            prefix += ["taskset", "-c", cores]
        if nice:
            prefix += ["nice", "-n", nice]
        return prefix

    def _build_send_cmd(self) -> list[str]:
        """Build the V4L2 → NDI send pipeline command."""
        binary = self._cfg["yuri_binary"]
        device = self._cfg["send_device"]
        stream = self._cfg["send_stream_name"]
        ndi_path = self._cfg["ndi_path"]
        return self._build_cmd_prefix() + [
            binary,
            f'v4l2_source[device="{device}"]',
            f'ndi_output[stream="{stream}"][ndi_path="{ndi_path}"]',
        ]

    def _build_receive_cmd(self) -> list[str]:
        """Build the NDI → GLX display receive pipeline command."""
        binary = self._cfg["yuri_binary"]
        stream = self._cfg["receive_stream_name"]
        ndi_path = self._cfg["ndi_path"]
        fullscreen = "true" if self._cfg["receive_fullscreen"] else "false"
        return self._build_cmd_prefix() + [
            binary,
            f'ndi_input[stream="{stream}"][ndi_path="{ndi_path}"]',
            "frame_info[]",
            f"glx_window[fullscreen={fullscreen}]",
        ]

    def _terminate_process(self, name: str) -> None:
        """SIGTERM a tracked process, wait, then SIGKILL if still alive."""
        proc = self._send_proc if name == "send" else self._recv_proc
        if proc is None or proc.poll() is not None:
            return
        self._log.info("Terminating NDI %s process (pid=%d)", name, proc.pid)
        proc.terminate()
        try:
            proc.wait(timeout=_TERMINATE_WAIT_S)
        except subprocess.TimeoutExpired:
            self._log.warning("NDI %s process did not exit — sending SIGKILL", name)
            proc.kill()
            proc.wait()
        if name == "send":
            self._send_proc = None
        else:
            self._recv_proc = None

    # ── Commands ──────────────────────────────────────────────────────────────

    def on_cmd_reset(self, payload_str: str) -> None:
        """Stop both processes and republish retained state."""
        try:
            payload = json.loads(payload_str) if payload_str else {}
            request_id = payload.get("request_id", "")
        except json.JSONDecodeError:
            request_id = ""
        self._terminate_process("send")
        self._terminate_process("receive")
        self._publish_all_retained()
        self.publish_result("reset", request_id, ok=True)

    def on_cmd_ping(self, payload_str: str) -> None:
        try:
            payload = json.loads(payload_str) if payload_str else {}
            request_id = payload.get("request_id", "")
        except json.JSONDecodeError:
            request_id = ""
        self.publish_result("ping", request_id, ok=True)

    def on_cmd_cfg_set(self, payload_str: str) -> None:
        """Apply config keys from payload[\"set\"]. Unknown keys are silently ignored."""
        try:
            payload = json.loads(payload_str) if payload_str else {}
            request_id = payload.get("request_id", "")
            set_dict = payload.get("set") or {}
        except json.JSONDecodeError:
            request_id = ""
            set_dict = {}

        if not isinstance(set_dict, dict):
            self.publish_cfg_set_result(
                request_id=request_id, ok=False,
                error="payload 'set' must be an object", ts=_utc_iso(),
            )
            return

        applied: dict[str, Any] = {}
        for key, value in set_dict.items():
            if key in _ALLOWED_CFG_KEYS:
                self._cfg[key] = value
                applied[key] = value

        self.publish_state()
        self.publish_cfg()
        self.publish_cfg_set_result(
            request_id=request_id, ok=True,
            applied=applied or None, ts=_utc_iso(),
        )

    def on_cmd_send_start(self, payload_str: str) -> None:
        """Start the NDI sender (V4L2 → NDI). Fails if already running."""
        try:
            payload = json.loads(payload_str) if payload_str else {}
            request_id = payload.get("request_id", "")
        except json.JSONDecodeError:
            request_id = ""

        if self._send_proc is not None and self._send_proc.poll() is None:
            self.publish_result("send/start", request_id, ok=False,
                                error="sender already running")
            return

        cmd = self._build_send_cmd()
        self._log.info("Starting NDI sender: %s", " ".join(cmd))
        try:
            self._send_proc = subprocess.Popen(
                cmd, env=self._build_env(),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception as exc:
            self._log.error("Failed to start NDI sender: %s", exc)
            self.publish_result("send/start", request_id, ok=False, error=str(exc))
            return

        self.publish_state()
        self.publish_result("send/start", request_id, ok=True)

    def on_cmd_send_stop(self, payload_str: str) -> None:
        """Stop the NDI sender. Idempotent — not an error if not running."""
        try:
            payload = json.loads(payload_str) if payload_str else {}
            request_id = payload.get("request_id", "")
        except json.JSONDecodeError:
            request_id = ""

        self._terminate_process("send")
        self.publish_state()
        self.publish_result("send/stop", request_id, ok=True)

    def on_cmd_receive_start(self, payload_str: str) -> None:
        """Start the NDI receiver (NDI → display). Requires receive_stream_name to be set."""
        try:
            payload = json.loads(payload_str) if payload_str else {}
            request_id = payload.get("request_id", "")
        except json.JSONDecodeError:
            request_id = ""

        if not str(self._cfg.get("receive_stream_name", "")).strip():
            self.publish_result("receive/start", request_id, ok=False,
                                error="receive_stream_name must be set via cfg/set before starting receiver")
            return

        if self._recv_proc is not None and self._recv_proc.poll() is None:
            self.publish_result("receive/start", request_id, ok=False,
                                error="receiver already running")
            return

        cmd = self._build_receive_cmd()
        self._log.info("Starting NDI receiver: %s", " ".join(cmd))
        try:
            self._recv_proc = subprocess.Popen(
                cmd, env=self._build_env(),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception as exc:
            self._log.error("Failed to start NDI receiver: %s", exc)
            self.publish_result("receive/start", request_id, ok=False, error=str(exc))
            return

        self.publish_state()
        self.publish_result("receive/start", request_id, ok=True)

    def on_cmd_receive_stop(self, payload_str: str) -> None:
        """Stop the NDI receiver. Idempotent."""
        try:
            payload = json.loads(payload_str) if payload_str else {}
            request_id = payload.get("request_id", "")
        except json.JSONDecodeError:
            request_id = ""

        self._terminate_process("receive")
        self.publish_state()
        self.publish_result("receive/stop", request_id, ok=True)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _publish_all_retained(self) -> None:
        self.publish_metadata()
        self.publish_status()
        self.publish_state()
        self.publish_cfg()
```

- [ ] **Step 2: Run tests — should still fail (package not installed)**

```bash
cd /Users/farahorfaly/Desktop/LUCID/lucid-component-ndi
pip install -e ".[dev]" -q
python -m pytest tests/test_contract.py -v 2>&1 | tail -20
```

Expected: tests run but may fail on assertions — address any real failures before proceeding.

- [ ] **Step 3: Run tests and confirm all pass**

```bash
cd /Users/farahorfaly/Desktop/LUCID/lucid-component-ndi
python -m pytest tests/test_contract.py -v
```

Expected: All tests pass. Fix any failures before continuing.

- [ ] **Step 4: Commit**

```bash
cd /Users/farahorfaly/Desktop/LUCID/lucid-component-ndi
git add src/lucid_component_ndi/ tests/test_contract.py pyproject.toml
git rm -r src/lucid_component_example/
git commit -m "feat: implement NDI component with libyuri subprocess control"
```

---

## Task 5: Verify entry point registration

- [ ] **Step 1: Confirm entry point is discovered**

```bash
cd /Users/farahorfaly/Desktop/LUCID/lucid-component-ndi
pip install -e . -q
python -c "
from importlib.metadata import entry_points
eps = entry_points(group='lucid.components')
names = [ep.name for ep in eps]
print('Entry points:', names)
assert 'ndi' in names, 'ndi entry point missing!'
print('OK')
"
```

Expected:
```
Entry points: ['ndi']
OK
```

- [ ] **Step 2: Confirm the class loads cleanly**

```bash
python -c "
from lucid_component_ndi import NDIComponent
print('NDIComponent:', NDIComponent)
print('component_id:', NDIComponent.__new__(NDIComponent).__class__.__name__)
"
```

Expected: No import errors.

---

## Verification (on RPi with hardware)

```bash
# Install on the Pi
pip install -e .

# Verify the agent will discover it — ensure the agent registry JSON includes:
# {"component_id": "ndi", "enabled": true, "config": {"receive_stream_name": ""}}

# Start the LUCID agent, then via MQTT:

# 1. Configure the receiver stream name
# Topic: lucid/agents/{agent_id}/components/ndi/cmd/cfg/set
# Payload: {"request_id": "cfg-1", "set": {"receive_stream_name": "CAMERA (stream_name)"}}

# 2. Start the NDI sender (V4L2 → NDI broadcast)
# Topic: lucid/agents/{agent_id}/components/ndi/cmd/send/start
# Payload: {"request_id": "send-1"}

# 3. Start the NDI receiver (NDI → display)
# Topic: lucid/agents/{agent_id}/components/ndi/cmd/receive/start
# Payload: {"request_id": "recv-1"}

# 4. Monitor state
# Topic: lucid/agents/{agent_id}/components/ndi/state
# Expected: {"send_active": true, "send_pid": ..., "receive_active": true, "receive_pid": ...}

# 5. Monitor telemetry
# Topic: lucid/agents/{agent_id}/components/ndi/telemetry/send_running
# Topic: lucid/agents/{agent_id}/components/ndi/telemetry/receive_running
```
