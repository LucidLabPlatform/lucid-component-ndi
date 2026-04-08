# lucid-component-ndi

> **Status: UNIMPLEMENTED STUB**
>
> This repo is a placeholder. The source code is a copy of `lucid-component-template` with the package renamed. NDI video integration has not been implemented.

## Intended Purpose

When implemented, this component would provide:
- NDI (Network Device Interface) video stream capture and output on a LUCID agent
- Publish NDI stream metadata as LUCID telemetry
- Accept LUCID commands to switch NDI sources, start/stop streams

## Current State

- `component_id` is still `"example"` (placeholder value from template)
- Entry-point group uses `"lucid.components"` instead of `"lucid_components"` (inconsistency with other components — to be fixed when implemented)
- No NDI-specific logic exists

## To Implement

1. Choose an NDI library (e.g., `ndi-python`, `pyndi`)
2. Rename class from `ExampleComponent` to `NdiComponent`
3. Set `component_id = "ndi"`
4. Implement `_start()` / `_stop()` with NDI stream management
5. Add capabilities for NDI source selection and stream control
6. Fix entry-point group to `"lucid_components"`
