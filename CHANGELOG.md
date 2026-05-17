# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [3.0.0-beta.5](https://github.com/afalfallaj/ha_gimdow_ble/compare/v3.0.0-beta.4...v3.0.0-beta.5) (2026-05-17)


### ### Features

* add tracking for last good sequence number and code name in protocol ([7f8ff12](https://github.com/afalfallaj/ha_gimdow_ble/commit/7f8ff12a8bae4e5caec697ba69c17fbe7afe6978))

## [3.0.0-beta.4](https://github.com/afalfallaj/ha_gimdow_ble/compare/v3.0.0-beta.3...v3.0.0-beta.4) (2026-05-17)


### ### Refactoring

* simplify double command logic to always send two commands for lock state changes ([a3dad8f](https://github.com/afalfallaj/ha_gimdow_ble/commit/a3dad8fccca285ec3c3d7deaa93c64762f46553e))

## [3.0.0-beta.3](https://github.com/afalfallaj/ha_gimdow_ble/compare/v3.0.0-beta.2...v3.0.0-beta.3) (2026-05-17)


### ### Features

* enhance double command logic to support forced two attempts for locking/unlocking ([2c7ac66](https://github.com/afalfallaj/ha_gimdow_ble/commit/2c7ac6620936a04f265e1f6dc65c2cbd03f553e2))

## [3.0.0-beta.2](https://github.com/afalfallaj/ha_gimdow_ble/compare/v3.0.0-beta.1...v3.0.0-beta.2) (2026-05-17)


### ### Refactoring

* replace AES with cryptography library for encryption and decryption ([d87e98b](https://github.com/afalfallaj/ha_gimdow_ble/commit/d87e98bc9d3a39ac4eb01cfb4137ec5c26e8388f))

## [3.0.0-beta.1](https://github.com/afalfallaj/ha_gimdow_ble/compare/v3.0.0-beta.0...v3.0.0-beta.1) (2026-05-16)


### ### Features

* add auto-merge step for pre-release PRs in release workflow ([6e0d03c](https://github.com/afalfallaj/ha_gimdow_ble/commit/6e0d03cb6c0d24c4ed57bda8700ee28bd29fb077))


### ### Bug Fixes

* correct JSON parsing for auto-merge pre-release PR step ([350579d](https://github.com/afalfallaj/ha_gimdow_ble/commit/350579d7cc52dceb3b78f1b3ae06e84e398cded8))
* specify repository in auto-merge command for pre-release PRs ([17d341f](https://github.com/afalfallaj/ha_gimdow_ble/commit/17d341f224665a2884d70f9b4a9008cd4a79b65e))

## [3.0.0-beta.0](https://github.com/afalfallaj/ha_gimdow_ble/compare/v2.0.5...v3.0.0-beta.0) (2026-05-16)

> ### ⚠ Breaking Changes
>
> - **Config entry version 1 → 4** (`async_migrate_entry` handles auto-migration on first HA restart). Credentials are now stored in `entry.data`; only user-tunable options remain in `entry.options`. No manual action needed, but a downgrade to v2.0.x will leave the entry in an unreadable state.
> - **Unknown-state action values renamed** — existing option-flow settings are silently reset to the new default (`confirm_last`) after migration:
>   - `resolve` → `confirm_last` *(new default)*
>   - `skip` → *removed*
>   - `force_lock` → `force_lock_twice`
>   - *(new)* `double_on_action`
> - **`sensor.<device>_battery` entity removed and replaced by `sensor.<device>_battery_state`** — entity ID changes; automations and dashboards referencing the old entity must be updated.
> - **`sensor.<device>_unlock_fingerprint`, `_unlock_card`, `_unlock_password` entities removed** — update any automations or dashboards that reference these sensors.
> - **Device manufacturer changed from "Tuya" to "Gimdow"** — Home Assistant may show a duplicate device entry until the old one is cleared from the device registry.
> - **`door_sensor` field removed from the login and re-auth config-flow steps** — it is now exclusively in the Options flow. Re-running the initial setup will no longer offer this field; set it via *Configure* on the integration instead.

### Added
- **Config entry auto-migration** (`async_migrate_entry`) — upgrades entries from version 1 through 4 automatically on HA restart; no user action required.
- **`auto_lock_delay_fallback` option** — configurable fallback delay (default 30 s, range 5–300 s) used when the device does not report an auto-lock timer via the cloud.
- **`battery_state` sensor** replaces the old `battery` sensor, with translated state labels (`high`, `normal`, `low`, `poweroff`) and a dedicated `poweroff` icon.
- **`poweroff` battery state** — maps new firmware-reported `poweroff` value to a dedicated icon (`mdi:battery-off-outline`).
- **`change_direction` switch translated states** — UI now shows "Standard" / "Reversed" instead of raw on/off.
- **Unknown-state action inline description** — the options-flow selector now shows a contextual explanation of each strategy's reconnect behaviour.

### Changed
- **Coordinator switched from polling (60 s) to event-driven** — state updates are pushed by BLE notifications; `update_interval` is now `None`, eliminating unnecessary polls.
- **Unknown-state recovery on reconnect** — the lock manager fires `on_connected()` on every BLE reconnect and automatically resolves state per the configured strategy (`confirm_last` or `force_lock_twice`), without waiting for a manual command.
- **`pycountry` dependency removed** — country lookup now uses the built-in `TUYA_COUNTRIES` list, removing an external package requirement.
- **Cloud cache moved from module-level to `hass.data`** — eliminates cross-restart cache pollution and makes the cache properly scoped to each HA instance.
- **Reauth flow handles missing config entry** — aborts with `reason="unknown_entry"` instead of raising an unhandled exception.
- **`get_login_from_cache` accepts an optional address** — prefers the cache entry that actually holds the target device, preventing cross-account credential pre-fill in multi-account setups.
- **Entity availability model simplified** — `battery_state`, `signal_strength`, and `door_sensor` stay available while BLE is sleeping (last-known values); all other entities follow live BLE connectivity.

### Fixed
- Cloud cache write races under concurrent `await` calls — now protected by a per-`hass` `asyncio.Lock` instead of an unguarded module-level dict.
- Coordinator callbacks and disconnect timer were never unregistered on entry unload — `GimdowBLECoordinator.stop()` is now called from `async_unload_entry`.
- `HASSGimdowBLEDeviceManager` was initialised with `entry.options` instead of `entry.data` — credentials were not passed correctly on setup.
- Config-flow country lookup no longer raises `IndexError` on an unrecognised country code — returns an `invalid_auth` error instead.

---

## [2.0.5] - 2026-04-17

### Changed
- Removed redundant notification retry logic that could cause duplicate sends.

---

## [2.0.4] - 2026-04-17

### Added
- Retry logic for BLE notification subscription with proxy-aware delay before retrying.

### Changed
- Improved transition state handling in lock manager to reduce false "locking/unlocking" states.

### Fixed
- Stale ESPHome proxy CONNECTING state after a failed handshake — a `PROXY_CLEAR_DELAY` is now applied before the next reconnect attempt.

---

## [2.0.3] - 2026-04-16

### Fixed
- Activity logbook entries were sometimes attributed to the wrong source (HA vs. manual).

---

## [2.0.2] - 2026-04-04

### Added
- **State Transition Timeout** option — configurable maximum wait time (default 60 s) before an in-progress lock/unlock falls back to Unknown state.

---

## [2.0.1] - 2026-03-27

### Added
- **Bluetooth adapter selection** — choose a specific adapter or ESPHome proxy from the options flow.
- **Unknown State Action** option (initial implementation with `resolve`, `skip`, `force_lock` strategies).

---

## [2.0.0] - 2026-03-27

Complete rewrite of the BLE core layer.

### Added
- Full lock state machine with `is_locking`, `is_unlocking`, and `is_jammed` properties.
- Unknown state resolution — unlock-first cycle to reach a deterministic state.
- Door sensor integration with safety interlock and auto-lock on close.
- Virtual auto-lock with configurable delay.
- `_send_control_datapoint_wait_for_echo` helper for reliable command confirmation.
- `get_lock_state` helper and `_is_resolving` guard to prevent concurrent resolution.
- Diagnostics snapshot for structured error reporting.

### Changed
- BLE layer extracted into `gimdow_ble/` sub-package (connection, protocol, device, lock manager, datapoints).
- Session keep-alive added to maintain BLE connection between user operations.
- Reconnect logic with exponential backoff (up to 10 attempts, 5-minute ceiling).

### Fixed
- Deadbolt extension blocked when door sensor reports open during unknown state resolution.
- Concurrent state resolution attempts now guarded by `_is_resolving` flag.

---

## [1.4.1] - 2026-02-12

### Fixed
- Disconnect on control echo timeout to avoid silent stale connections.
- Lock/unlock state flag resets corrected after timeout.

---

## [1.4.0] - 2026-02-13

### Added
- Bluetooth adapter list now populated from discovered BLE service sources.

### Changed
- Enhanced BLE connection robustness and packet handling.

---

## [1.3.0] - 2026-01-27

### Added
- Unknown lock state resolution with force-unlock sequence and `is_locking` property.
- Unlock command echo confirmation.
- Increased lock state confirmation timeout for slower mechanical locks.

---

[Unreleased]: https://github.com/afalfallaj/ha_gimdow_ble/compare/v2.0.6-beta.3...HEAD
[2.0.6-beta.3]: https://github.com/afalfallaj/ha_gimdow_ble/compare/v2.0.5...v2.0.6-beta.3
[2.0.5]: https://github.com/afalfallaj/ha_gimdow_ble/compare/v2.0.4...v2.0.5
[2.0.4]: https://github.com/afalfallaj/ha_gimdow_ble/compare/v2.0.3...v2.0.4
[2.0.3]: https://github.com/afalfallaj/ha_gimdow_ble/compare/v2.0.2...v2.0.3
[2.0.2]: https://github.com/afalfallaj/ha_gimdow_ble/compare/v2.0.1...v2.0.2
[2.0.1]: https://github.com/afalfallaj/ha_gimdow_ble/compare/v2.0.0...v2.0.1
[2.0.0]: https://github.com/afalfallaj/ha_gimdow_ble/compare/v1.4.1...v2.0.0
[1.4.1]: https://github.com/afalfallaj/ha_gimdow_ble/compare/v1.4.0...v1.4.1
[1.4.0]: https://github.com/afalfallaj/ha_gimdow_ble/compare/v1.3.0...v1.4.0
[1.3.0]: https://github.com/afalfallaj/ha_gimdow_ble/releases/tag/v1.3.0
