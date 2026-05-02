---
title: Restart-on-busy for NDI start commands
date: 2026-05-02
status: proposed
---

# Restart-on-busy for `start_receive` and `start_send`

## Problem

When the NDI component sends `cmd/receive/start` or `cmd/send/start` while the
corresponding pipeline is already running, the helper daemon currently rejects
the command with `{"ok": false, "error": "<...> pipeline already running"}`.

The desired behavior is: treat a duplicate start as an implicit restart —
log a warning, stop the running process, then start fresh and report success.

## Scope

In scope:
- `PipelineState.start_receive` in `src/lucid_component_ndi/helper_server.py`
- `PipelineState.start_send` in `src/lucid_component_ndi/helper_server.py`

Out of scope:
- Any other component
- Other helper commands (`stop_*`, `reset`, `ping`, `get_status`)
- Component-side logic in `component.py` (no changes needed; it just sees `ok: true`)

## Design

In each `start_*` method, when the existing process is alive:

1. Log at WARNING level: `"<pipeline> pipeline already running (PID %d), restarting"`.
2. Reuse the existing private stop path to terminate the running process.
   The stop path already handles `SIGTERM` → 5s wait → `SIGKILL` fallback and
   clears the slot.
3. Fall through to the existing spawn code path.

All steps run under `self._lock`, which the start method already holds, so the
restart is atomic from the helper's perspective.

The current `_stop` helper acquires the lock itself, so to avoid re-entrant
locking we will factor out an unlocked `_stop_locked(pipeline)` that performs
the termination assuming the caller already holds the lock. `_stop` becomes a
thin wrapper that takes the lock and calls `_stop_locked`. `start_receive` /
`start_send` call `_stop_locked` directly.

## Behavior changes

| Scenario | Before | After |
|---|---|---|
| `start_receive` with no running receive | spawn, return `ok: true` | unchanged |
| `start_receive` while receive running | return `ok: false, error: "receive pipeline already running"` | log warning, stop old, spawn new, return `ok: true` |
| `start_send` analogous | analogous | analogous |
| `start_*` with new spawn raising | return `ok: false, error: <exc>` | unchanged |

The MQTT contract for the component is unchanged in shape — only the outcome
of the duplicate-start case flips from failure to success.

## Tests

Add to `tests/`:
- `start_receive` while a receive process is already running stops the original
  process and returns `ok: true` with the new PID.
- `start_send` analogous.
- A WARNING log line is emitted on the restart path.

Existing tests that asserted on the "already running" error must be updated to
the new behavior.

## Risks

- **Brief output gap during restart.** Stopping and respawning yuri_simple
  produces a momentary blackout on the consumer. Acceptable: the same gap
  already happens when the operator manually issues `stop` then `start`.
- **Re-entrant locking** if `_stop` is called from inside `start_*`. Mitigated
  by introducing `_stop_locked` as described above.
