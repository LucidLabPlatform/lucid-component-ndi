"""NDI helper daemon — runs as the display user, manages yuri_simple processes.

Listens on a Unix socket for JSON-RPC commands from the NDI component.
Runs as the X session owner so yuri_simple can access the display.
"""
from __future__ import annotations

import json
import logging
import os
import signal
import socket
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("lucid.ndi.helper")

SOCKET_PATH = os.environ.get("LUCID_NDI_SOCKET", "/run/lucid/ndi.sock")
SOCKET_GROUP = "lucid"


class PipelineState:
    """Manages yuri_simple subprocess lifecycle."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._receive_proc: Optional[subprocess.Popen] = None
        self._send_proc: Optional[subprocess.Popen] = None

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

    def stop_receive(self) -> dict:
        with self._lock:
            return self._stop_locked("receive")

    def start_send(self, cmd: list[str], env: dict[str, str]) -> dict:
        with self._lock:
            if self._send_proc and self._send_proc.poll() is None:
                return {"ok": False, "error": "send pipeline already running"}
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

    def stop_send(self) -> dict:
        with self._lock:
            return self._stop_locked("send")

    def reset(self) -> dict:
        with self._lock:
            self._kill("receive")
            self._kill("send")
            return {"ok": True}

    def get_status(self) -> dict:
        with self._lock:
            recv = self._receive_proc
            send = self._send_proc
            return {
                "ok": True,
                "receive_active": recv is not None and recv.poll() is None,
                "receive_pid": recv.pid if recv and recv.poll() is None else None,
                "send_active": send is not None and send.poll() is None,
                "send_pid": send.pid if send and send.poll() is None else None,
            }

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

    def _kill(self, pipeline: str) -> None:
        proc = self._receive_proc if pipeline == "receive" else self._send_proc
        if proc and proc.poll() is None:
            try:
                proc.kill()
                proc.wait(timeout=3.0)
            except Exception:
                pass
        if pipeline == "receive":
            self._receive_proc = None
        else:
            self._send_proc = None

    def shutdown(self) -> None:
        with self._lock:
            self._kill("receive")
            self._kill("send")


def _handle_request(state: PipelineState, req: dict[str, Any]) -> dict[str, Any]:
    rid = req.get("id", 0)
    cmd = req.get("cmd", "")

    if cmd == "ping":
        result = {"ok": True}
    elif cmd == "get_status":
        result = state.get_status()
    elif cmd == "start_receive":
        result = state.start_receive(req.get("args", []), req.get("env", {}))
    elif cmd == "stop_receive":
        result = state.stop_receive()
    elif cmd == "start_send":
        result = state.start_send(req.get("args", []), req.get("env", {}))
    elif cmd == "stop_send":
        result = state.stop_send()
    elif cmd == "reset":
        result = state.reset()
    else:
        result = {"ok": False, "error": f"unknown command: {cmd}"}

    result["id"] = rid
    return result


def _handle_client(conn: socket.socket, state: PipelineState) -> None:
    try:
        conn.settimeout(10.0)
        data = b""
        while b"\n" not in data:
            chunk = conn.recv(4096)
            if not chunk:
                return
            data += chunk
        line = data.split(b"\n", 1)[0]
        req = json.loads(line.decode("utf-8"))
        resp = _handle_request(state, req)
        conn.sendall((json.dumps(resp) + "\n").encode("utf-8"))
    except Exception:
        logger.exception("Client handler error")
    finally:
        conn.close()


def _setup_socket(path: str) -> socket.socket:
    sock_path = Path(path)
    sock_path.parent.mkdir(parents=True, exist_ok=True)
    if sock_path.exists():
        sock_path.unlink()

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(str(sock_path))
    sock.listen(5)
    os.chmod(str(sock_path), 0o660)

    # Set group to lucid if possible
    try:
        import grp
        gid = grp.getgrnam(SOCKET_GROUP).gr_gid
        os.chown(str(sock_path), -1, gid)
        os.chown(str(sock_path.parent), -1, gid)
        os.chmod(str(sock_path.parent), 0o775)
    except (KeyError, PermissionError):
        logger.warning("Could not set socket group to '%s'", SOCKET_GROUP)

    return sock


def main() -> None:
    logger.info("NDI helper starting (socket: %s, user: %s)", SOCKET_PATH, os.getenv("USER", "?"))
    state = PipelineState()
    sock = _setup_socket(SOCKET_PATH)
    logger.info("Listening on %s", SOCKET_PATH)

    stop = threading.Event()

    def _signal_handler(signum: int, _: Any) -> None:
        logger.info("Received signal %d, shutting down", signum)
        stop.set()
        state.shutdown()

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    sock.settimeout(1.0)
    while not stop.is_set():
        try:
            conn, _ = sock.accept()
            threading.Thread(
                target=_handle_client, args=(conn, state), daemon=True,
            ).start()
        except socket.timeout:
            continue
        except Exception:
            if not stop.is_set():
                logger.exception("Accept error")

    sock.close()
    Path(SOCKET_PATH).unlink(missing_ok=True)
    logger.info("NDI helper stopped")


if __name__ == "__main__":
    main()
