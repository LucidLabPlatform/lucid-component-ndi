"""Contract tests for NDIComponent: lifecycle, state, commands."""
import json
from unittest.mock import patch

import pytest
from lucid_component_base import ComponentContext, ComponentStatus

from lucid_component_ndi import NDIComponent


class RecordingMQTT:
    def __init__(self):
        self.calls: list[dict] = []

    def publish(self, topic: str, payload, *, qos: int = 0, retain: bool = False) -> None:
        self.calls.append({"topic": topic, "payload": payload, "qos": qos, "retain": retain})

    def last_payload_for(self, suffix: str) -> dict | None:
        for c in reversed(self.calls):
            if c["topic"].endswith(suffix):
                return json.loads(c["payload"])
        return None


def _make(cfg: dict | None = None) -> tuple[NDIComponent, RecordingMQTT]:
    mqtt = RecordingMQTT()
    ctx = ComponentContext.create(
        agent_id="test-agent",
        base_topic="lucid/agents/test-agent",
        component_id="ndi",
        mqtt=mqtt,
        config=cfg or {},
    )
    return NDIComponent(ctx), mqtt


def test_component_id():
    comp, _ = _make()
    assert comp.component_id == "ndi"


def test_initial_status():
    comp, _ = _make()
    assert comp.state.status == ComponentStatus.STOPPED


def test_capabilities():
    comp, _ = _make()
    caps = comp.capabilities()
    assert "receive/start" in caps
    assert "receive/stop" in caps
    assert "send/start" in caps
    assert "send/stop" in caps
    assert "reset" in caps
    assert "ping" in caps


def test_state_payload_structure():
    comp, _ = _make()
    state = comp.get_state_payload()
    assert "receive_active" in state
    assert "receive_pid" in state
    assert "receive_stream_name" in state
    assert "send_active" in state
    assert "send_pid" in state
    assert "send_stream_name" in state
    assert state["receive_active"] is False
    assert state["send_active"] is False


def test_cfg_payload():
    comp, _ = _make()
    cfg = comp.get_cfg_payload()
    assert cfg["ndi_path"] == "/usr/local/lib/libndi.so"
    assert cfg["yuri_binary"] == "yuri_simple"
    assert cfg["receive_stream_name"] == ""
    assert cfg["receive_fullscreen"] is True


def test_config_override_from_context():
    comp, _ = _make({"receive_stream_name": "TEST (north)", "ndi_path": "/custom/libndi.so"})
    cfg = comp.get_cfg_payload()
    assert cfg["receive_stream_name"] == "TEST (north)"
    assert cfg["ndi_path"] == "/custom/libndi.so"


def test_start_stop_lifecycle():
    comp, mqtt = _make()
    comp.start()
    assert comp.state.status == ComponentStatus.RUNNING
    assert comp.state.started_at is not None
    comp.stop()
    assert comp.state.status == ComponentStatus.STOPPED
    assert comp.state.stopped_at is not None


def test_start_stop_idempotent():
    comp, _ = _make()
    comp.start()
    comp.start()
    comp.stop()
    comp.stop()


def test_ping():
    comp, mqtt = _make()
    comp.start()
    comp.on_cmd_ping(json.dumps({"request_id": "r1"}))
    result = mqtt.last_payload_for("evt/ping/result")
    assert result is not None
    assert result["ok"] is True
    assert result["request_id"] == "r1"
    comp.stop()


def test_reset_kills_processes():
    comp, mqtt = _make()
    comp.start()
    comp.on_cmd_reset(json.dumps({"request_id": "r2"}))
    result = mqtt.last_payload_for("evt/reset/result")
    assert result["ok"] is True
    state = comp.get_state_payload()
    assert state["receive_active"] is False
    assert state["send_active"] is False
    comp.stop()


def test_receive_start_no_stream_name():
    comp, mqtt = _make()
    comp.start()
    comp.on_cmd_receive_start(json.dumps({"request_id": "r3"}))
    result = mqtt.last_payload_for("evt/receive/start/result")
    assert result["ok"] is False
    assert "not configured" in result["error"]
    comp.stop()


def test_cfg_set_applies():
    comp, mqtt = _make()
    comp.start()
    comp.on_cmd_cfg_set(json.dumps({
        "request_id": "r4",
        "set": {"receive_stream_name": "NEW_STREAM (east)"},
    }))
    result = mqtt.last_payload_for("evt/cfg/set/result")
    assert result["ok"] is True
    assert result["applied"]["receive_stream_name"] == "NEW_STREAM (east)"
    assert comp.get_cfg_payload()["receive_stream_name"] == "NEW_STREAM (east)"
    comp.stop()


def test_cfg_set_unknown_key():
    comp, mqtt = _make()
    comp.start()
    comp.on_cmd_cfg_set(json.dumps({
        "request_id": "r5",
        "set": {"bogus_key": 42},
    }))
    result = mqtt.last_payload_for("evt/cfg/set/result")
    assert result["ok"] is False
    assert "unknown" in result["error"]
    comp.stop()


def test_build_receive_cmd():
    comp, _ = _make({"receive_stream_name": "HOST (east)"})
    cmd = comp._build_receive_cmd()
    assert cmd[0] == "yuri_simple"
    assert any("ndi_input" in arg for arg in cmd)
    assert any("glx_window" in arg for arg in cmd)


def test_build_receive_cmd_with_affinity():
    comp, _ = _make({"receive_stream_name": "HOST (east)", "cpu_cores": "1-3", "nice_level": 5})
    cmd = comp._build_receive_cmd()
    assert cmd[0] == "nice"
    assert "-n" in cmd
    assert "taskset" in cmd
