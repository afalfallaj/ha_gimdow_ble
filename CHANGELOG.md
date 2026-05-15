# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

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

[Unreleased]: https://github.com/afalfallaj/ha_gimdow_ble/compare/v2.0.5...HEAD
[2.0.5]: https://github.com/afalfallaj/ha_gimdow_ble/compare/v2.0.4...v2.0.5
[2.0.4]: https://github.com/afalfallaj/ha_gimdow_ble/compare/v2.0.3...v2.0.4
[2.0.3]: https://github.com/afalfallaj/ha_gimdow_ble/compare/v2.0.2...v2.0.3
[2.0.2]: https://github.com/afalfallaj/ha_gimdow_ble/compare/v2.0.1...v2.0.2
[2.0.1]: https://github.com/afalfallaj/ha_gimdow_ble/compare/v2.0.0...v2.0.1
[2.0.0]: https://github.com/afalfallaj/ha_gimdow_ble/compare/v1.4.1...v2.0.0
[1.4.1]: https://github.com/afalfallaj/ha_gimdow_ble/compare/v1.4.0...v1.4.1
[1.4.0]: https://github.com/afalfallaj/ha_gimdow_ble/compare/v1.3.0...v1.4.0
[1.3.0]: https://github.com/afalfallaj/ha_gimdow_ble/releases/tag/v1.3.0
