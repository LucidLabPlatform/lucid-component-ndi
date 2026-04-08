"""NDI component — wraps yuri_simple for NDI receive/send pipelines.

Manages yuri_simple subprocesses for:
- Receive: NDI stream → fullscreen GLX display (projector use case)
- Send: V4L2 camera → NDI stream broadcast

Commands: receive/start, receive/stop, send/start, send/stop, reset, ping, cfg/set.
Telemetry: receive_running, send_running (gated, polled every 5s).
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
from datetime import datetime, timezone
from typing import Any, Optional

from lucid_component_base import Component, ComponentContext


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class NDIComponent(Component):
    """NDI video stream controller wrapping yuri_simple."""

    _MONITOR_INTERVAL_S = 5.0

    # Default configuration
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
        # Apply any config passed from agent
        if context.config:
            for k, v in context.config.items():
                if k in self._cfg:
                    self._cfg[k] = v

        self._receive_proc: Optional[subprocess.Popen] = None
        self._send_proc: Optional[subprocess.Popen] = None
        self._stop_event = threading.Event()
        self._monitor_thread: Optional[threading.Thread] = None

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

    def get_state_payload(self) -> dict[str, Any]:
        return {
            "receive_active": self._receive_proc is not None and self._receive_proc.poll() is None,
            "receive_pid": self._receive_proc.pid if self._receive_proc and self._receive_proc.poll() is None else None,
            "receive_stream_name": self._cfg["receive_stream_name"],
            "send_active": self._send_proc is not None and self._send_proc.poll() is None,
            "send_pid": self._send_proc.pid if self._send_proc and self._send_proc.poll() is None else None,
            "send_stream_name": self._cfg["send_stream_name"],
        }

    def get_cfg_payload(self) -> dict[str, Any]:
        return dict(self._cfg)

    # ── lifecycle ──────────────────────────────────────────────

    def _start(self) -> None:
        self._publish_all_retained()
        self._stop_event.clear()
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop, name="NDIMonitor", daemon=True,
        )
        self._monitor_thread.start()
        self._log.info("Started component: %s", self.component_id)

    def _stop(self) -> None:
        self._stop_event.set()
        if self._monitor_thread:
            self._monitor_thread.join(timeout=3.0)
            self._monitor_thread = None
        self._kill_proc("receive")
        self._kill_proc("send")
        self._log.info("Stopped component: %s", self.component_id)

    def _publish_all_retained(self) -> None:
        self.publish_metadata()
        self.publish_status()
        self.publish_state()
        self.set_telemetry_config({
            "receive_running": {"enabled": True, "interval_s": 5, "change_threshold_percent": 0},
            "send_running": {"enabled": True, "interval_s": 5, "change_threshold_percent": 0},
        })
        self.publish_cfg()

    # ── monitor loop ──────────────────────────────────────────

    def _monitor_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
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

    # ── process management ────────────────────────────────────

    def _build_receive_cmd(self) -> list[str]:
        stream = self._cfg["receive_stream_name"]
        ndi_path = self._cfg["ndi_path"]
        fullscreen = "true" if self._cfg["receive_fullscreen"] else "false"
        yuri = self._cfg["yuri_binary"]

        cmd = [yuri]
        cmd.append(f"ndi_input[stream={stream}][ndi_path={ndi_path}]")
        cmd.append("frame_info[]")
        cmd.append(f"glx_window[fullscreen={fullscreen}]")
        return self._wrap_with_affinity(cmd)

    def _build_send_cmd(self) -> list[str]:
        device = self._cfg["send_device"]
        stream = self._cfg["send_stream_name"]
        ndi_path = self._cfg["ndi_path"]
        yuri = self._cfg["yuri_binary"]

        cmd = [yuri]
        cmd.append(f'v4l2_source[device="{device}"]')
        cmd.append(f'ndi_output[stream="{stream}"][ndi_path={ndi_path}]')
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

    def _start_proc(self, pipeline: str) -> tuple[bool, str]:
        """Start a yuri_simple pipeline. Returns (ok, error_or_empty)."""
        if pipeline == "receive":
            if self._receive_proc and self._receive_proc.poll() is None:
                return False, "receive pipeline already running"
            stream = self._cfg["receive_stream_name"]
            if not stream:
                return False, "receive_stream_name not configured"
            cmd = self._build_receive_cmd()
        elif pipeline == "send":
            if self._send_proc and self._send_proc.poll() is None:
                return False, "send pipeline already running"
            stream = self._cfg["send_stream_name"]
            if not stream:
                return False, "send_stream_name not configured"
            cmd = self._build_send_cmd()
        else:
            return False, f"unknown pipeline: {pipeline}"

        env = dict(os.environ)
        env["NDI_PATH"] = self._cfg["ndi_path"]
        env["XDG_RUNTIME_DIR"] = self._cfg["xdg_runtime_dir"]
        # Ensure DISPLAY is set for GLX
        if "DISPLAY" not in env:
            env["DISPLAY"] = ":0"

        self._log.info("Starting %s pipeline: %s", pipeline, " ".join(cmd))
        try:
            proc = subprocess.Popen(
                cmd,
                env=env,
                cwd="/tmp",
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
        except Exception as exc:
            return False, str(exc)

        if pipeline == "receive":
            self._receive_proc = proc
        else:
            self._send_proc = proc

        self.publish_state()
        self._log.info("%s pipeline started (PID %d)", pipeline, proc.pid)
        return True, ""

    def _stop_proc(self, pipeline: str) -> tuple[bool, str]:
        """Stop a yuri_simple pipeline. Returns (ok, error_or_empty)."""
        proc = self._receive_proc if pipeline == "receive" else self._send_proc
        if not proc or proc.poll() is not None:
            # Already stopped — clear ref
            if pipeline == "receive":
                self._receive_proc = None
            else:
                self._send_proc = None
            self.publish_state()
            return True, ""

        self._log.info("Stopping %s pipeline (PID %d)", pipeline, proc.pid)
        try:
            os.kill(proc.pid, signal.SIGTERM)
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            self._log.warning("SIGTERM timeout, sending SIGKILL to PID %d", proc.pid)
            proc.kill()
            proc.wait(timeout=3.0)
        except Exception as exc:
            return False, str(exc)

        if pipeline == "receive":
            self._receive_proc = None
        else:
            self._send_proc = None
        self.publish_state()
        self._log.info("%s pipeline stopped", pipeline)
        return True, ""

    def _kill_proc(self, pipeline: str) -> None:
        """Best-effort kill of a pipeline process."""
        proc = self._receive_proc if pipeline == "receive" else self._send_proc
        if proc and proc.poll() is None:
            try:
                proc.kill()
                proc.wait(timeout=3.0)
            except Exception:
                self._log.exception("Failed to kill %s proc", pipeline)
        if pipeline == "receive":
            self._receive_proc = None
        else:
            self._send_proc = None

    # ── command handlers ──────────────────────────────────────

    def on_cmd_reset(self, payload_str: str) -> None:
        try:
            payload = json.loads(payload_str) if payload_str else {}
            request_id = payload.get("request_id", "")
        except json.JSONDecodeError:
            request_id = ""
        self._kill_proc("receive")
        self._kill_proc("send")
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
        # Allow overriding stream name in the command
        if "stream_name" in payload:
            self._cfg["receive_stream_name"] = payload["stream_name"]
        ok, error = self._start_proc("receive")
        self.publish_result("receive/start", request_id, ok=ok, error=error or None)

    def on_cmd_receive_stop(self, payload_str: str) -> None:
        try:
            payload = json.loads(payload_str) if payload_str else {}
            request_id = payload.get("request_id", "")
        except json.JSONDecodeError:
            request_id = ""
        ok, error = self._stop_proc("receive")
        self.publish_result("receive/stop", request_id, ok=ok, error=error or None)

    def on_cmd_send_start(self, payload_str: str) -> None:
        try:
            payload = json.loads(payload_str) if payload_str else {}
            request_id = payload.get("request_id", "")
        except json.JSONDecodeError:
            payload, request_id = {}, ""
        if "stream_name" in payload:
            self._cfg["send_stream_name"] = payload["stream_name"]
        ok, error = self._start_proc("send")
        self.publish_result("send/start", request_id, ok=ok, error=error or None)

    def on_cmd_send_stop(self, payload_str: str) -> None:
        try:
            payload = json.loads(payload_str) if payload_str else {}
            request_id = payload.get("request_id", "")
        except json.JSONDecodeError:
            request_id = ""
        ok, error = self._stop_proc("send")
        self.publish_result("send/stop", request_id, ok=ok, error=error or None)

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
