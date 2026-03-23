"""
Example LUCID component — template for new components.

Publishes retained metadata, status, state, cfg.
Commands: cmd/reset, cmd/ping, cmd/cfg/set → evt/<action>/result.

Copy this package, rename to your component (e.g. lucid_component_led), then:
- Replace component_id and state keys with your domain (e.g. brightness, color).
- Implement _start/_stop (hardware init, optional background loop).
- In on_cmd_cfg_set, apply payload["set"] to your config/state and publish_state/publish_cfg.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from lucid_component_base import Component, ComponentContext


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ExampleComponent(Component):
    """
    Template component: minimal state and full command handlers.

    Retained: metadata, status, state, cfg.
    Stream: logs (if logs_enabled). Optional: add telemetry in a loop.
    Commands: reset, ping, cfg/set.
    """

    def __init__(self, context: ComponentContext) -> None:
        super().__init__(context)
        self._log = context.logger()
        self._logs_enabled = False # Default to disabled for now
        # TODO: add your own state, e.g. self._brightness = 0.0

    @property
    def component_id(self) -> str:
        return "example"

    def capabilities(self) -> list[str]:
        return ["reset", "ping"]

    def metadata(self) -> dict[str, Any]:
        out = super().metadata()
        out["capabilities"] = self.capabilities()
        return out

    def get_state_payload(self) -> dict[str, Any]:
        """Live state for retained state topic. Replace keys with your domain (e.g. brightness, color)."""
        # TODO: return your actual state, e.g. {"brightness": self._brightness, "color": self._color}
        return {"value": 0}

    def _start(self) -> None:
        self._publish_all_retained()
        # TODO: init hardware, start background loop if needed
        self._log.info("Started component: %s", self.component_id)

    def _stop(self) -> None:
        # TODO: stop background loop, release hardware
        self._log.info("Stopped component: %s", self.component_id)

    def _publish_all_retained(self) -> None:
        self.publish_metadata()
        self.publish_status()
        self.publish_state()
        self.set_telemetry_config({})
        self.publish_cfg()

    def on_cmd_reset(self, payload_str: str) -> None:
        """Handle cmd/reset → evt/reset/result."""
        try:
            payload = json.loads(payload_str) if payload_str else {}
            request_id = payload.get("request_id", "")
        except json.JSONDecodeError:
            request_id = ""
        # TODO: reset your component state/hardware if needed
        self.publish_result("reset", request_id, ok=True, error=None)

    def on_cmd_ping(self, payload_str: str) -> None:
        """Handle cmd/ping → evt/ping/result."""
        try:
            payload = json.loads(payload_str) if payload_str else {}
            request_id = payload.get("request_id", "")
        except json.JSONDecodeError:
            request_id = ""
        self.publish_result("ping", request_id, ok=True, error=None)

    def on_cmd_cfg_set(self, payload_str: str) -> None:
        """Handle cmd/cfg/set → evt/cfg/set/result. Apply payload[\"set\"] and republish state/cfg."""
        try:
            payload = json.loads(payload_str) if payload_str else {}
            request_id = payload.get("request_id", "")
            set_dict = payload.get("set") or {}
        except json.JSONDecodeError:
            request_id = ""
            set_dict = {}

        if not isinstance(set_dict, dict):
            self.publish_cfg_set_result(
                request_id=request_id,
                ok=False,
                applied=None,
                error="payload 'set' must be an object",
                ts=_utc_iso(),
            )
            return

        applied: dict[str, Any] = {}

        # Standard: logs_enabled
        if "logs_enabled" in set_dict:
            self._logs_enabled = bool(set_dict["logs_enabled"])
            applied["logs_enabled"] = self._logs_enabled

        # TODO: apply your own config from set_dict (e.g. brightness, color), update applied, then drive hardware
        # Example: if "brightness" in set_dict: self._brightness = clamp(set_dict["brightness"]); applied["brightness"] = self._brightness

        self.publish_state()
        self.publish_cfg()
        self.publish_cfg_set_result(
            request_id=request_id,
            ok=True,
            applied=applied if applied else None,
            error=None,
            ts=_utc_iso(),
        )
