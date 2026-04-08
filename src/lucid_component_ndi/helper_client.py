"""Client for the NDI helper daemon (Unix socket JSON-RPC)."""
from __future__ import annotations

import json
import os
import socket
from typing import Any

SOCKET_PATH = os.environ.get("LUCID_NDI_SOCKET", "/run/lucid/ndi.sock")
TIMEOUT_S = 10.0


def _request(cmd: str, **params: Any) -> dict[str, Any]:
    """Send a command to the NDI helper and return the response."""
    req = {"id": 1, "cmd": cmd, **params}
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(TIMEOUT_S)
    try:
        sock.connect(SOCKET_PATH)
        sock.sendall((json.dumps(req) + "\n").encode("utf-8"))
        data = b""
        while b"\n" not in data:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
        return json.loads(data.split(b"\n", 1)[0].decode("utf-8"))
    finally:
        sock.close()


def ping() -> dict[str, Any]:
    return _request("ping")


def get_status() -> dict[str, Any]:
    return _request("get_status")


def start_receive(args: list[str], env: dict[str, str]) -> dict[str, Any]:
    return _request("start_receive", args=args, env=env)


def stop_receive() -> dict[str, Any]:
    return _request("stop_receive")


def start_send(args: list[str], env: dict[str, str]) -> dict[str, Any]:
    return _request("start_send", args=args, env=env)


def stop_send() -> dict[str, Any]:
    return _request("stop_send")


def reset() -> dict[str, Any]:
    return _request("reset")


def is_available() -> bool:
    """Check if the helper daemon socket exists and responds."""
    try:
        resp = ping()
        return resp.get("ok", False)
    except (FileNotFoundError, ConnectionRefusedError, OSError):
        return False
