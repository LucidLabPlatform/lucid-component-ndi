"""
Minimal contract tests: instantiate with fake context, start(), stop(), assert state transitions.
Prevents broken components from being committed.
"""
import pytest
from lucid_component_base import ComponentContext, ComponentStatus

from lucid_component_example import ExampleComponent


def _fake_context() -> ComponentContext:
    class FakeMqtt:
        def publish(self, topic: str, payload, *, qos: int = 0, retain: bool = False) -> None:
            pass

    return ComponentContext.create(
        agent_id="test-agent",
        base_topic="lucid/agents/test-agent",
        component_id="example",
        mqtt=FakeMqtt(),
        config={},
    )


def test_component_instantiation():
    ctx = _fake_context()
    comp = ExampleComponent(ctx)
    assert comp.component_id == "example"
    assert comp.state.status == ComponentStatus.STOPPED


def test_get_state_payload_returns_dict():
    ctx = _fake_context()
    comp = ExampleComponent(ctx)
    state = comp.get_state_payload()
    assert isinstance(state, dict)
    assert "value" in state


def test_capabilities():
    ctx = _fake_context()
    comp = ExampleComponent(ctx)
    caps = comp.capabilities()
    assert isinstance(caps, list)
    assert "reset" in caps
    assert "ping" in caps


def test_start_stop_state_transitions():
    ctx = _fake_context()
    comp = ExampleComponent(ctx)

    comp.start()
    assert comp.state.status == ComponentStatus.RUNNING
    assert comp.state.started_at is not None

    comp.stop()
    assert comp.state.status == ComponentStatus.STOPPED
    assert comp.state.stopped_at is not None
