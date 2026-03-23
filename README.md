# lucid-component-example

Template LUCID component. Copy this repo and rename it to create new components (e.g. LED, sensor, actuator).

## Purpose

This package is a **skeleton** that matches the LUCID contract: retained metadata, status, state, cfg; commands reset, ping, cfg/set; and optional telemetry. Use it as the starting point for new components. You do **not** need to modify `lucid-agent-core` or `lucid-component-base`.

## What’s included

| Item | Purpose |
|------|--------|
| **Makefile** | `setup-venv`, `test`, `test-coverage`, `build`, `clean` |
| **pyproject.toml** | Package name, deps (lucid-component-base), entry point, setuptools-scm version |
| **src/lucid_component_example/** | Package to rename; contains `component.py` and `__init__.py` |
| **component.py** | Full skeleton: state, cfg, on_cmd_reset/ping/cfg_set, _start/_stop, TODOs for your logic |
| **tests/test_contract.py** | Minimal contract tests (instantiate, start/stop, state payload, capabilities) |
| **README.md** | This file; steps to create a new component |
| **.gitignore** | Python, build, venv, pytest, IDE, OS junk |

## Creating a new component from this template

1. **Copy the repo**  
   Clone or copy `lucid-component-template` to a new directory (e.g. `lucid-component-led`).

2. **Rename the package**  
   - Rename `src/lucid_component_example/` to `src/lucid_component_<name>/` (e.g. `lucid_component_led`).  
   - In `pyproject.toml`: set `name = "lucid-component-<name>"`, update `include = ["lucid_component_<name>*"]`, and set the entry point, e.g.  
     `led = "lucid_component_led.component:LedComponent"`.  
   - Rename the class in `component.py` (e.g. `ExampleComponent` → `LedComponent`) and set `component_id` (e.g. `return "led"`).  
   - Update `__init__.py` to import and export the new class.  
   - In **Makefile**, set `PACKAGE = lucid_component_<name>` so `make test-coverage` uses the right path.

3. **Define your state**  
   In `get_state_payload()`, return a dict of live values (e.g. `{"brightness": self._brightness, "color": self._color, "on": self._on}`). Publish state on start and whenever it changes (e.g. after applying cfg/set or in a background loop).

4. **Add your config**  
   In `on_cmd_cfg_set()`, read `payload["set"]`, apply your keys (e.g. brightness, color), update your internal state and hardware, then call `publish_state()`, `publish_cfg()`, and `publish_cfg_set_result(...)`. Optionally extend cfg in `_publish_all_retained()` so retained cfg matches your schema.

5. **Optional: telemetry**  
   If you want to stream metrics over time, add a background loop (like fixture-cpu), call `publish_telemetry(metric_name, value)` when gating allows, and configure `telemetry.metrics` in cfg and in `_publish_all_retained()`.

6. **Hardware**  
   In `_start()` / `_stop()` and inside `on_cmd_cfg_set`, call your driver or a stub. The rest is MQTT-only.

7. **Install via the agent**  
   Build a wheel, publish a release (e.g. GitHub), then use the agent’s **cmd/components/install** with your wheel URL. The agent will install the package and register the entry point; no changes to agent-core required.

## Configuration keys (template)

- **logs_enabled** (bool): Enable MQTT log streaming. Add your own keys in `on_cmd_cfg_set` and document them here.

## MQTT topics used

Under `lucid/agents/<agent_id>/components/example/` (replace `example` with your `component_id`):

- **Retained:** metadata, status, state, cfg  
- **Stream:** logs (batched), optional telemetry/\<metric>  
- **Commands:** cmd/reset, cmd/ping, cmd/cfg/set  
- **Results:** evt/reset/result, evt/ping/result, evt/cfg/set/result  

## Development and tests

**Using the Makefile (recommended):**

```bash
make setup-venv   # create .venv, install deps (run once)
make test        # unit + integration tests
make test-coverage   # tests with coverage
make build       # build wheel and sdist
make clean       # remove build artifacts
```

**Without Make:** install in editable mode with dev deps (requires network for `lucid-component-base` from git), then run pytest:

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

Or use `uv sync` then `uv run pytest tests/ -v` if your environment resolves the git dependency.

## Versioning

Version is derived from Git tags via [setuptools_scm](https://github.com/pypa/setuptools_scm). Do not set `version` in `pyproject.toml` manually.

## Entry point

Registered under the `lucid.components` entry point group for discovery by agent-core:

- **Entry point name:** `example` (change when you rename the component)  
- **Module path:** `lucid_component_example.component:ExampleComponent`  

After renaming, ensure the entry point in `pyproject.toml` matches your package and class name.
