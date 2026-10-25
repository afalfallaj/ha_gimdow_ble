# AGENTS.md — AI Agent Guide

## Project

Home Assistant custom integration for the **Gimdow A1 Pro Max BLE** smart lock.
- HA integration root: `custom_components/gimdow_ble/`
- BLE sub-package: `custom_components/gimdow_ble/gimdow_ble/`

---

## Versioning — Rules for AI Agents

### Never do these
- **Never manually edit version strings** in any file.
- **Never create git tags manually** — the GitHub Action owns all tags.
- **Never create or edit a `CHANGELOG.md` file**. Release notes are dynamically generated on the GitHub Releases page.

### Version Automation
Versioning is powered by `.github/workflows/tag_and_release.yml` using `anothrNick/github-tag-action`.
When a push occurs:
- `dev` branch: The action bumps the patch version with a `-dev` suffix (e.g., `v1.0.1-dev.0`), updates `manifest.json` and `__init__.py`, and publishes a GitHub Pre-Release.
- `main` branch: The action creates a stable tag (e.g., `v1.0.1`), updates `manifest.json` and `__init__.py`, and publishes an Official GitHub Release.

### Commit Message Format — Required
All commits **must** use [Conventional Commits](https://www.conventionalcommits.org/). The action reads these to determine the next version bump.

```
<type>(<optional scope>): <short description>
```

| Commit prefix | Version bump |
|---|---|
| `feat:` | minor — `2.0.x` → `2.1.0` |
| `fix:` | patch — `2.0.5` → `2.0.6` |
| `refactor:`, `perf:`, `docs:`, `chore:` | none |
| `feat!:` or `BREAKING CHANGE:` footer | **major** |

### Release Flow
1. **Beta Releases**: Push to `dev`. The action automatically tags, updates manifests, and publishes a Pre-Release.
2. **Stable Releases**: Open a PR from `dev` to `main`. Use **squash and merge** with a conventional commit title (e.g., `feat: ...`). The action automatically tags, updates manifests, and publishes a stable Release.

---

## Configuration Entities (Master State)

The Gimdow lock may send stale or default data for configuration settings (e.g., volume, auto-lock time, direction) upon reconnecting or after a power cycle. To combat this, Home Assistant acts as the **master state** for configuration entities (DP31, DP33, DP36, DP78).
- **Master State Pattern:** Configuration entities (`select.py`, `number.py`, `switch.py`) maintain a master state in memory, which is persisted across restarts via `RestoreEntity`.
- **Overwrite Stale Pushes:** Inside `_handle_coordinator_update()`, if the device pushes a value that mismatches the HA master state, HA ignores the push and immediately queues a command to overwrite the lock's datapoint with HA's master state.
- **Exceptions:** This logic strictly applies to settings. It explicitly does **NOT** apply to the physical lock position (`DP47`), which relies purely on device truth (see below).

---

## Hardware Findings (DP47 / Lock State)

These findings are confirmed by hardware test results (`test-res.txt`).

| Finding | Description |
|---|---|
| DP47 on manual operation | **DP47 IS pushed** when the user manually operates the lock while BLE is connected. Echo arrives in ~5–9 s depending on direction. State is tracked correctly without any special handling. |
| DP47 missed on manual unlock | Occasionally the DP47 push for a manual unlock never arrives (or arrives late) even with BLE connected. With a door sensor, `on_door_changed()` treats door closed → open while DP47 still reads locked as a missed manual unlock: the bolt blocks the door, so it must be unlocked. Only the local DP47 value is corrected (nothing is sent), so auto-lock re-locks after the door closes. Skipped on the first door event after start-up and when the state is unknown/`TIMEOUT_UNKNOWN`. A later real DP47 push overwrites it. |
| DP47 on reconnect | DP47 is **NOT** pushed by the device after a BLE reconnect. The integration clears the stale value on reconnect and relies on the configured unknown-state strategy to resolve position. |
| DP47 double-command timing | When two commands are sent (double-command pattern), the device pushes DP47 after the second motor operation completes. Observed echo times: ~8.7 s (lock) and ~17.3 s (unlock). Well within the 60 s default timeout. |
| No HA-state restore for lock position | `lock.py` deliberately does **not** restore last-known "locked"/"unlocked" from Home Assistant's entity state history (`RestoreEntity`) on startup. A restored value can go stale during any HA-down window (the lock can be operated manually while HA is off) and can't be corroborated by a fresh device read on reconnect, since DP47 isn't re-pushed (see above) — reasserting it via a double-command risks overwriting a legitimate manual operation. `GimdowBLELockManager._last_known_state` is seeded **exclusively** from live DP47 pushes via `on_coordinator_update()`; after a full HA restart it starts unknown, so `confirm_last`/`force_lock_twice` stay unknown until the device reports a real reading (they still resolve immediately from a live reconnect within an already-running session, once traffic has populated `_last_known_state`). **Do not reintroduce `RestoreEntity`/`async_get_last_state()`-based state restore in `lock.py`** — this was deliberately removed, not an oversight. |

---

## Auto-Lock & Unknown State

The virtual auto-lock (`GimdowBLELockManager.start_auto_lock_timer()`) must treat an unknown lock state as **armed, not blocked**. What the fired timer does is decided by the "action when lock state is unknown" setting, exactly as for any other lock command.

| Rule | Detail |
|---|---|
| No unknown guard on the timer | **Do not re-add an `is_locked is None` check (or a `TIMEOUT_UNKNOWN` exclusion) to `start_auto_lock_timer()`.** That guard ("N7") made auto-lock silently do nothing whenever DP47 was unknown, even with Double on Action. Only `LOCKING` (a transition already in flight) and an already-locked lock suppress arming. |
| Outcome per strategy | When the timer fires with state unknown: **Double on Action** → double lock command. **Confirm Last / Force Lock Twice** → nothing extra is sent; their reconnect resolution owns the unknown state. `is_explicit_lock` in `_handle_unknown_state()` covers **only** a door-close pending lock, not auto-lock. |
| `_lock_state()` scope | `_lock_state()` (returns `None` during `TIMEOUT_UNKNOWN`, matching the entity) is used by the auto-lock path only. `lock()`/`unlock()` deliberately keep the raw DP47 read: a manual Lock during `TIMEOUT_UNKNOWN` must not cancel the in-flight `_attempt_timeout_recovery` task and then be dropped. |
| Where arming happens | Doors, setting/delay changes, `unlock()`, and `arm_auto_lock_if_idle()` from the coordinator's disconnected→connected flip in `lock.py` (startup, and reconnects where DP47 was cleared). **Not** from `on_connected()`: it also runs on every BLE handshake (`_on_ble_reconnected`) and would re-arm a failed double-command each time. |
| Safety net stays narrow | The `on_coordinator_update()` safety net stays at `is_locked is False`. Widening it to unknown re-arms the timer on every DP push and loops arm → fire → ignore under Confirm Last / Force Lock Twice. |
| Attribution | `_auto_lock_callback` sets `_pending_action_source = AUTO`. If the command is not dispatched (strategy ignore, resolution already running) the source is cleared by `_drop_auto_action_source()`; `update_attribution()` consumes it on `None → locked`. Otherwise a later manual operation is mislabeled "Auto Lock". |

---

## CI/CD Files

| File | Purpose |
|---|---|
| `.github/workflows/tag_and_release.yml` | Auto-generates tags, bumps manifests, and publishes GitHub Releases on push to `main` or `dev` |

There is currently no CI workflow running `hassfest` or HACS validation (`validate.yml` was removed); validate manually before a release if a change touches `manifest.json`, `hacs.json`, or translations.

`hacs.json`'s `"homeassistant"` minimum-version floor is **manually maintained** — `tag_and_release.yml` never touches it. Bump it whenever a change starts relying on a newer HA API (e.g. it was raised to `2025.8.0` because `config_flow.py` uses `OptionsFlowWithReload`, added in that HA release). A stale floor doesn't fail CI; it just lets HACS install the integration onto HA versions where an import silently breaks.
