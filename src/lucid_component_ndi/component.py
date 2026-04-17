"""NDI component — wraps yuri_simple for NDI receive/send pipelines.

Delegates process management to the NDI helper daemon (runs as display user).
The helper owns yuri_simple subprocesses and has X11/Wayland display access.

Commands: receive/start, receive/stop, send/start, send/stop, reset, ping, cfg/set.
Telemetry: receive_running, send_running (gated, polled every 5s).
"""
from __future__ import annotations

import json
import threading
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Optional

from lucid_component_base import Component, ComponentContext

from . import helper_client


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class NDIComponent(Component):
    """NDI video stream controller via helper daemon."""

    _MONITOR_INTERVAL_S = 5.0

    _DEFAULTS: dict[str, Any] = {
        "ndi_path": "/usr/local/lib/libndi.so",
        "yuri_binary": "yuri_simple",
        "xdg_runtime_dir": "/run/user/1000",
        "cpu_cores": "",
        "nice_level": 0,
        "receive_stream_name": "",
        "receive_fullscreen": True,
        "send_device": "/dev/video0",
        "send_stream_name": "lucid-ndi",
    }

    def __init__(self, context: ComponentContext) -> None:
        super().__init__(context)
        self._log = context.logger()
        self._cfg: dict[str, Any] = dict(self._DEFAULTS)
        if context.config:
            for k, v in context.config.items():
                if k in self._cfg:
                    self._cfg[k] = v
        self._stop_event = threading.Event()
        self._monitor_thread: Optional[threading.Thread] = None
        self._last_status: dict[str, Any] = {}

    @property
    def component_id(self) -> str:
        return "ndi"

    def capabilities(self) -> list[str]:
        return [
            "reset", "ping",
            "receive/start", "receive/stop",
            "send/start", "send/stop",
        ]

    def metadata(self) -> dict[str, Any]:
        out = super().metadata()
        out["capabilities"] = self.capabilities()
        return out

    def schema(self) -> dict[str, Any]:
        s = deepcopy(super().schema())

        s["publishes"]["state"]["fields"].update({
            "receive_active": {"type": "boolean"},
            "receive_pid": {"type": "integer"},
            "receive_stream_name": {"type": "string"},
            "send_active": {"type": "boolean"},
            "send_pid": {"type": "integer"},
            "send_stream_name": {"type": "string"},
        })

        s["publishes"]["cfg"]["fields"].update({
            "ndi_path": {"type": "string"},
            "yuri_binary": {"type": "string"},
            "xdg_runtime_dir": {"type": "string"},
            "cpu_cores": {"type": "string"},
            "nice_level": {"type": "integer"},
            "receive_stream_name": {"type": "string"},
            "receive_fullscreen": {"type": "boolean"},
            "send_device": {"type": "string"},
            "send_stream_name": {"type": "string"},
        })

        s["publishes"]["telemetry/receive_running"] = {
            "fields": {"value": {"type": "boolean"}},
        }
        s["publishes"]["telemetry/send_running"] = {
            "fields": {"value": {"type": "boolean"}},
        }

        s["subscribes"]["cmd/receive/start"] = {
            "fields": {"stream_name": {"type": "string", "description": "Override receive stream name"}},
        }
        s["subscribes"]["cmd/receive/stop"] = {"fields": {}}
        s["subscribes"]["cmd/send/start"] = {
            "fields": {"stream_name": {"type": "string", "description": "Override send stream name"}},
        }
        s["subscribes"]["cmd/send/stop"] = {"fields": {}}

        return s

    def get_state_payload(self) -> dict[str, Any]:
        status = self._last_status
        return {
            "receive_active": status.get("receive_active", False),
            "receive_pid": status.get("receive_pid"),
            "receive_stream_name": self._cfg["receive_stream_name"],
            "send_active": status.get("send_active", False),
            "send_pid": status.get("send_pid"),
            "send_stream_name": self._cfg["send_stream_name"],
        }

    def get_cfg_payload(self) -> dict[str, Any]:
        return dict(self._cfg)

    # ── lifecycle ──────────────────────────────────────────────

    def _start(self) -> None:
        self._refresh_status()
        self._publish_all_retained()
        self._stop_event.clear()
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop, name="NDIMonitor", daemon=True,
        )
        self._monitor_thread.start()
        self._log.info("Started component: %s (helper available: %s)",
                       self.component_id, helper_client.is_available())

    def _stop(self) -> None:
        self._stop_event.set()
        if self._monitor_thread:
            self._monitor_thread.join(timeout=3.0)
            self._monitor_thread = None
        self._log.info("Stopped component: %s", self.component_id)

    def _publish_all_retained(self) -> None:
        self.publish_metadata()
        self.publish_schema()
        self.publish_status()
        self.publish_state()
        self.set_telemetry_config({
            "receive_running": {"enabled": False, "interval_s": 0.1, "change_threshold_percent": 0},
            "send_running": {"enabled": False, "interval_s": 0.1, "change_threshold_percent": 0},
        })
        self.publish_cfg()

    # ── monitor loop ──────────────────────────────────────────

    def _refresh_status(self) -> None:
        try:
            self._last_status = helper_client.get_status()
        except Exception:
            self._last_status = {}

    def _monitor_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._refresh_status()
                state = self.get_state_payload()
                self.publish_state(state)
                recv = state["receive_active"]
                send = state["send_active"]
                if self.should_publish_telemetry("receive_running", recv):
                    self.publish_telemetry("receive_running", recv)
                if self.should_publish_telemetry("send_running", send):
                    self.publish_telemetry("send_running", send)
            except Exception:
                self._log.exception("Monitor loop error")
            if self._stop_event.wait(self._MONITOR_INTERVAL_S):
                break

    # ── pipeline commands ─────────────────────────────────────

    def _build_receive_cmd(self) -> list[str]:
        stream = self._cfg["receive_stream_name"]
        ndi_path = self._cfg["ndi_path"]
        fullscreen = "true" if self._cfg["receive_fullscreen"] else "false"
        yuri = self._cfg["yuri_binary"]
        cmd = [yuri,
               f"ndi_input[stream={stream}][ndi_path={ndi_path}]",
               "frame_info[]",
               f"glx_window[fullscreen={fullscreen}]"]
        return self._wrap_with_affinity(cmd)

    def _build_send_cmd(self) -> list[str]:
        device = self._cfg["send_device"]
        stream = self._cfg["send_stream_name"]
        ndi_path = self._cfg["ndi_path"]
        yuri = self._cfg["yuri_binary"]
        cmd = [yuri,
               f'v4l2_source[device="{device}"]',
               f'ndi_output[stream="{stream}"][ndi_path={ndi_path}]']
        return self._wrap_with_affinity(cmd)

    def _wrap_with_affinity(self, cmd: list[str]) -> list[str]:
        cores = self._cfg.get("cpu_cores", "")
        nice = self._cfg.get("nice_level", 0)
        wrapped = list(cmd)
        if cores:
            wrapped = ["taskset", "-c", str(cores)] + wrapped
        if nice and int(nice) != 0:
            wrapped = ["nice", "-n", str(nice)] + wrapped
        return wrapped

    def _build_env(self) -> dict[str, str]:
        return {
            "NDI_PATH": self._cfg["ndi_path"],
            "XDG_RUNTIME_DIR": self._cfg["xdg_runtime_dir"],
            "DISPLAY": ":0",
        }

    # ── command handlers ──────────────────────────────────────

    def on_cmd_reset(self, payload_str: str) -> None:
        try:
            payload = json.loads(payload_str) if payload_str else {}
            request_id = payload.get("request_id", "")
        except json.JSONDecodeError:
            request_id = ""
        try:
            helper_client.reset()
        except Exception as exc:
            self.publish_result("reset", request_id, ok=False, error=str(exc))
            return
        self._refresh_status()
        self.publish_state()
        self.publish_result("reset", request_id, ok=True, error=None)

    def on_cmd_ping(self, payload_str: str) -> None:
        try:
            payload = json.loads(payload_str) if payload_str else {}
            request_id = payload.get("request_id", "")
        except json.JSONDecodeError:
            request_id = ""
        self.publish_result("ping", request_id, ok=True, error=None)

    def on_cmd_receive_start(self, payload_str: str) -> None:
        try:
            payload = json.loads(payload_str) if payload_str else {}
            request_id = payload.get("request_id", "")
        except json.JSONDecodeError:
            payload, request_id = {}, ""
        if "stream_name" in payload:
            self._cfg["receive_stream_name"] = payload["stream_name"]
        if not self._cfg["receive_stream_name"]:
            self.publish_result("receive/start", request_id, ok=False,
                                error="receive_stream_name not configured")
            return
        try:
            result = helper_client.start_receive(
                self._build_receive_cmd(), self._build_env())
        except Exception as exc:
            self.publish_result("receive/start", request_id, ok=False, error=str(exc))
            return
        self._refresh_status()
        self.publish_state()
        ok = result.get("ok", False)
        self.publish_result("receive/start", request_id, ok=ok,
                            error=result.get("error") if not ok else None)

    def on_cmd_receive_stop(self, payload_str: str) -> None:
        try:
            payload = json.loads(payload_str) if payload_str else {}
            request_id = payload.get("request_id", "")
        except json.JSONDecodeError:
            request_id = ""
        try:
            result = helper_client.stop_receive()
        except Exception as exc:
            self.publish_result("receive/stop", request_id, ok=False, error=str(exc))
            return
        self._refresh_status()
        self.publish_state()
        ok = result.get("ok", False)
        self.publish_result("receive/stop", request_id, ok=ok,
                            error=result.get("error") if not ok else None)

    def on_cmd_send_start(self, payload_str: str) -> None:
        try:
            payload = json.loads(payload_str) if payload_str else {}
            request_id = payload.get("request_id", "")
        except json.JSONDecodeError:
            payload, request_id = {}, ""
        if "stream_name" in payload:
            self._cfg["send_stream_name"] = payload["stream_name"]
        if not self._cfg["send_stream_name"]:
            self.publish_result("send/start", request_id, ok=False,
                                error="send_stream_name not configured")
            return
        try:
            result = helper_client.start_send(
                self._build_send_cmd(), self._build_env())
        except Exception as exc:
            self.publish_result("send/start", request_id, ok=False, error=str(exc))
            return
        self._refresh_status()
        self.publish_state()
        ok = result.get("ok", False)
        self.publish_result("send/start", request_id, ok=ok,
                            error=result.get("error") if not ok else None)

    def on_cmd_send_stop(self, payload_str: str) -> None:
        try:
            payload = json.loads(payload_str) if payload_str else {}
            request_id = payload.get("request_id", "")
        except json.JSONDecodeError:
            request_id = ""
        try:
            result = helper_client.stop_send()
        except Exception as exc:
            self.publish_result("send/stop", request_id, ok=False, error=str(exc))
            return
        self._refresh_status()
        self.publish_state()
        ok = result.get("ok", False)
        self.publish_result("send/stop", request_id, ok=ok,
                            error=result.get("error") if not ok else None)

    def on_cmd_cfg_set(self, payload_str: str) -> None:
        request_id, set_dict, parse_error = self._parse_cfg_set_payload(payload_str)
        if parse_error:
            self.publish_cfg_set_result(
                request_id=request_id, ok=False, applied=None,
                error=parse_error, ts=_utc_iso(), action="cfg/set",
            )
            return

        applied: dict[str, Any] = {}
        unknown: list[str] = []
        for key, val in set_dict.items():
            if key in self._cfg:
                self._cfg[key] = val
                applied[key] = val
            else:
                unknown.append(key)

        if unknown:
            self.publish_cfg_set_result(
                request_id=request_id, ok=False, applied=applied or None,
                error=f"unknown cfg key(s): {', '.join(sorted(unknown))}",
                ts=_utc_iso(), action="cfg/set",
            )
            return

        self.publish_state()
        self.publish_cfg()
        self.publish_cfg_set_result(
            request_id=request_id, ok=True, applied=applied or None,
            error=None, ts=_utc_iso(), action="cfg/set",
        )
