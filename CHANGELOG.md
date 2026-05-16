# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [3.0.0](https://github.com/afalfallaj/ha_gimdow_ble/compare/ha-gimdow-ble-v2.0.5...ha-gimdow-ble-v3.0.0) (2026-05-16)


### ⚠ BREAKING CHANGES

* overhaul integration improvements, add test suite, and migrate CI to release-please

### ### Features

* Add `_send_control_datapoint_wait_for_echo` helper and refactor `resolve_unknown_state` to use it for more robust unlock command handling. ([6c53bd4](https://github.com/afalfallaj/ha_gimdow_ble/commit/6c53bd4c62b3229e50678a9a3a8213fcf1ae8ae8))
* add `EntityCategory` import to button platform ([2b0b058](https://github.com/afalfallaj/ha_gimdow_ble/commit/2b0b058eb1269721ece2e590849893ede7249d10))
* Add `is_unlocking` state and property to prevent reporting jammed status during unlock operations. ([8b0cf99](https://github.com/afalfallaj/ha_gimdow_ble/commit/8b0cf9937a52ae0ebdd04eaf2450532eb43ac73e))
* Add attribution tracking for lock and unlock actions, distinguishing between Home Assistant, auto-lock, and manual sources. ([2ca88d1](https://github.com/afalfallaj/ha_gimdow_ble/commit/2ca88d1b492109615139c3b5e67f8658fbbd37b3))
* add automated version bumping and release workflow for `dev` and `main` branches. ([8bdde4a](https://github.com/afalfallaj/ha_gimdow_ble/commit/8bdde4abf1fe1e027fcc3dabab6e33aa06a52d98))
* add Bluetooth adapter and unknown state action configuration options to setup flow. ([1c4bfe7](https://github.com/afalfallaj/ha_gimdow_ble/commit/1c4bfe73946d28dfb84d074465c138cedcda33df))
* Add door sensor binary sensor and refactor lock entity to use a dispatcher for door state updates, enabling auto-lock functionality. ([99137f8](https://github.com/afalfallaj/ha_gimdow_ble/commit/99137f805c6be6e8e160e5491b183c42b61ff91f))
* add Gimdow A1 Pro Max BLE integration, replacing the generic Tuya BLE component and updating documentation. ([9516fcc](https://github.com/afalfallaj/ha_gimdow_ble/commit/9516fccb7db5160b6f3b74cf8708577004c4ce30))
* Add Gimdow config support. ([59a97ed](https://github.com/afalfallaj/ha_gimdow_ble/commit/59a97ed7dea0506bf14db3a9c6119516631ebd12))
* Add HACS badge to README, update manifest codeowner, and bump integration version. ([7c13c52](https://github.com/afalfallaj/ha_gimdow_ble/commit/7c13c52e3d435465ce2d4f5d35d768a3d1439de2))
* Add individual button entities for recalibration and lock control, replacing the special function select entity. ([2e05245](https://github.com/afalfallaj/ha_gimdow_ble/commit/2e0524526412dabd2ade3b75c0f79dc12ebdc688))
* Add manual configuration option for device keys and update setup instructions. ([1b7ea94](https://github.com/afalfallaj/ha_gimdow_ble/commit/1b7ea94548348e38c8f9abce982a4cf3d83f58cd))
* Add manual device configuration, introduce periodic polling, and refactor DPType definitions. ([ae2e313](https://github.com/afalfallaj/ha_gimdow_ble/commit/ae2e3136cc94cc8c6e202f50b20a95b855c8860b))
* add RestoreEntity import ([a1af446](https://github.com/afalfallaj/ha_gimdow_ble/commit/a1af4468e1404cce3bd967f5a8681188ee1b5c6b))
* add retry logic for BLE notification subscription and improve error handling for connection status ([23a6567](https://github.com/afalfallaj/ha_gimdow_ble/commit/23a6567aa21075910474629c7e9b393dbe9fc0a1))
* add State Transition Timeout ([02c5b61](https://github.com/afalfallaj/ha_gimdow_ble/commit/02c5b61f61de950241a01b9d3f4b3c686aedd943))
* Add support for Gimdow A1 PRO MAX smart lock. ([d373986](https://github.com/afalfallaj/ha_gimdow_ble/commit/d373986e3995237ec03b2b87b2acbdf7d936975c))
* Add Tuya BLE lock platform and migrated gimdow lock entity from binary sensor and button platforms. ([3b482b8](https://github.com/afalfallaj/ha_gimdow_ble/commit/3b482b8b3739f887ca5f2ab1faf9055e58d3b19e))
* added pending lock using door sensor ([3904556](https://github.com/afalfallaj/ha_gimdow_ble/commit/390455633dbb82a4880abca7d865af3d6e7a21ed))
* Bump version to 1.1.0 and import new Gimdow BLE device and data point components. ([20bd788](https://github.com/afalfallaj/ha_gimdow_ble/commit/20bd788da7a828eb08401c8f03762d71e4a463f4))
* Door Position Awareness ([f63aa6f](https://github.com/afalfallaj/ha_gimdow_ble/commit/f63aa6ffb3e16fe65d03ecc80b82cf2594e268ae))
* Enhanced door sensor jammed state strategy. ([07c6440](https://github.com/afalfallaj/ha_gimdow_ble/commit/07c6440c352dd5123688021d9e26595a87f0fc68))
* Expand datapoint sending for protocol version 2 to use v3 method. ([ef99393](https://github.com/afalfallaj/ha_gimdow_ble/commit/ef993932e93632397ea48c12853563b9eeac874f))
* Implement state restoration and refine availability for Gimdow lock entities, and introduce periodic polling for the lock. ([e599d25](https://github.com/afalfallaj/ha_gimdow_ble/commit/e599d2535ca12c0a502946c0b0c1382a06dbb3ef))
* Implement state restoration for Tuya BLE lock entities by moving RestoreEntity to individual entity platforms. ([439c534](https://github.com/afalfallaj/ha_gimdow_ble/commit/439c53488f700221f30021069a6c089a68864a24))
* Implement unlock command echo confirmation and increase the lock state confirmation timeout. ([bb1b0f8](https://github.com/afalfallaj/ha_gimdow_ble/commit/bb1b0f8d27dce5b2ec15a5e5801635aaa5925f80))
* Introduce `get_lock_state` helper and prevent concurrent state resolution attempts using an `_is_resolving` flag. ([0a5df9c](https://github.com/afalfallaj/ha_gimdow_ble/commit/0a5df9c542c158401202e7f678ad9c04904fa16c))
* Introduce Bluetooth adapter selection and enhance GimdowBLE connection robustness and packet handling. ([c2d7da5](https://github.com/afalfallaj/ha_gimdow_ble/commit/c2d7da555aeb640c269c89786ecfd4c7c16ae04c))
* overhaul integration improvements, add test suite, and migrate CI to release-please ([ba81aba](https://github.com/afalfallaj/ha_gimdow_ble/commit/ba81aba08eb0dd72b6ecfdc47b88782b73ef1206))
* prefix calibration options with "Calibration: " and change "Auto-lock" to "Auto lock" in UI strings. ([07b5dd5](https://github.com/afalfallaj/ha_gimdow_ble/commit/07b5dd57ab62d02ab3add7da8228d34401989853))
* Rename configuration select entity to special function and remove its explicit value mapping. ([7e22ece](https://github.com/afalfallaj/ha_gimdow_ble/commit/7e22ecec2cbc1e35477c51df7ffdc08441704101))
* rename Gimdow BLE component references for clarity. ([585dbbd](https://github.com/afalfallaj/ha_gimdow_ble/commit/585dbbdc57a76754d08469aac8177a4b9da38270))
* Rewrite unknown lock state resolution with a force unlock sequence and `is_locking` property. ([ea6b307](https://github.com/afalfallaj/ha_gimdow_ble/commit/ea6b307fe42c59b979bc0b6bf22f7ef143a2fb12))


### ### Bug Fixes

* Activity logbook record accuracy ([3b7c75e](https://github.com/afalfallaj/ha_gimdow_ble/commit/3b7c75e0c2597a8c3a05c20b728bbdf53045cfae))
* Add disconnect and delay to reset lock position awareness before the second unlock attempt. ([e7b6127](https://github.com/afalfallaj/ha_gimdow_ble/commit/e7b61274adae525c06441307fc999f31d1d2a0d4))
* Add Kelvin color temperature support for lights, make regex patterns raw strings, and improve entity setup robustness. ([076d999](https://github.com/afalfallaj/ha_gimdow_ble/commit/076d999deec1a30cccc6fbe00f2bb94c22bc51c9))
* add target-branch parameter for dev configuration in release-please workflow ([e73850e](https://github.com/afalfallaj/ha_gimdow_ble/commit/e73850e43646aa2628ab96a3c81b579c847570aa))
* Adjust unknown state resolution to prevent locking an open door. ([0163e37](https://github.com/afalfallaj/ha_gimdow_ble/commit/0163e37e3caf06af47a60b265781c990f6f1da41))
* Auto Lock Improvements ([6c46ba5](https://github.com/afalfallaj/ha_gimdow_ble/commit/6c46ba594ddccd69f57b144a7b5095ebc81883df))
* config flow ([5b3a6bb](https://github.com/afalfallaj/ha_gimdow_ble/commit/5b3a6bb8ba6152aea74d6bdd6706adf6c7dac929))
* config flow ([65dc649](https://github.com/afalfallaj/ha_gimdow_ble/commit/65dc649fe2169a63b855d31c910bdde26aecfa09))
* config flow ([48e6700](https://github.com/afalfallaj/ha_gimdow_ble/commit/48e6700b42396fb90491959548747c0c59902ee6))
* Config Flow Sensor Default Value Logic ([9359cc0](https://github.com/afalfallaj/ha_gimdow_ble/commit/9359cc0c6eb2f6a7151995e7f1bc13387b984c9d))
* configflow ([b0c5eec](https://github.com/afalfallaj/ha_gimdow_ble/commit/b0c5eec5ea52b808ae7b65a16f6d1e910a097cdb))
* Directly unlock and return when the door is open during unknown state resolution. ([22a4ce2](https://github.com/afalfallaj/ha_gimdow_ble/commit/22a4ce252740b611ac7e41109c175c01cd37fb47))
* Disconnect Gimdow BLE device on control echo timeout and refine lock/unlock state flag resets. ([f9fc4e2](https://github.com/afalfallaj/ha_gimdow_ble/commit/f9fc4e21c3ed14aa4fb0ce939e644e886cdddcbc))
* Downgrade log levels for unexpected disconnections and response timeouts from warning/error to debug. ([4784ccd](https://github.com/afalfallaj/ha_gimdow_ble/commit/4784ccdcf559f06008d14433884650a97fb52988))
* Elevate BLE communication failure logging to error level. ([1c71871](https://github.com/afalfallaj/ha_gimdow_ble/commit/1c7187181ec5fd1308c24b5069c095ec4e3e3402))
* Elevate logging level for BLE communication and unexpected errors from debug to error. ([29ef184](https://github.com/afalfallaj/ha_gimdow_ble/commit/29ef184e38de953a39000aab2bcb1b3b0d30bf0c))
* Implement virtual auto-lock timer initiation and refine its start/execution conditions to prevent redundant actions. ([01e2b14](https://github.com/afalfallaj/ha_gimdow_ble/commit/01e2b146c0c58f4c9f3600676c9aa6c99b3d648c))
* Increase mechanical unlock cycle wait time to 10 seconds for reliable mechanical unlock completion. ([8f74044](https://github.com/afalfallaj/ha_gimdow_ble/commit/8f740442524827e0eb7db0c1a3494f36feb27831))
* IndentationError ([f1f7080](https://github.com/afalfallaj/ha_gimdow_ble/commit/f1f708097965d8761cb17248693c71e9ef4b7e2c))
* init.py ([9e3903b](https://github.com/afalfallaj/ha_gimdow_ble/commit/9e3903b534d4416497c7799a537a1e30805cee57))
* Populate device datapoints cache during state restoration for select, switch, and number entities. ([70abbd0](https://github.com/afalfallaj/ha_gimdow_ble/commit/70abbd013a5b6a7e178364827c9113953fff95bf))
* Prevent potential jamming by adding a pre-unlock step and state confirmation wait in `async_lock` when the lock state is unknown. ([7376b91](https://github.com/afalfallaj/ha_gimdow_ble/commit/7376b91901960daa1b1c46e731f9cfbba9b394c8))
* Re-enable 3-second delay to await mechanical unlock cycle completion. ([00ead11](https://github.com/afalfallaj/ha_gimdow_ble/commit/00ead114832ed70c68307af0f4120408715ee795))
* reduce disconnect sleep from 10s to 1s and reset the expected disconnect flag ([501e0d9](https://github.com/afalfallaj/ha_gimdow_ble/commit/501e0d9895ef603783bc2e18e6902b049fc78bed))
* Remove duplicate `product` parameter from `GimdowBLENumber` initialization. ([128b57a](https://github.com/afalfallaj/ha_gimdow_ble/commit/128b57abd36e4524975d2ae37d6ae009d97b7619))
* Remove duplicate parameters in switch.py ([935563c](https://github.com/afalfallaj/ha_gimdow_ble/commit/935563c9e81d4dc15d85d0bf97070429c072aa93))
* removing contact sensor ([9eabd0d](https://github.com/afalfallaj/ha_gimdow_ble/commit/9eabd0dfc280242afb8119708bd714537ba5d895))
* Tolerate device info request failures by logging a warning and continuing connection instead of terminating. ([c5d085b](https://github.com/afalfallaj/ha_gimdow_ble/commit/c5d085b3092260330b58f0c847bbdd9fa7a9a27a))


### ### Refactoring

* Change datapoint access for auto-lock delay from `.get()` to direct `[]` indexing. ([abdf3f6](https://github.com/afalfallaj/ha_gimdow_ble/commit/abdf3f6293283595bdd0893f79eb3019107e4dad))
* Convert entity control methods and lock polling to async/await ([aa1a4df](https://github.com/afalfallaj/ha_gimdow_ble/commit/aa1a4df36c926a4695f9f283b9066aba34069471))
* Eliminate duplicate door open check for auto-lock timer. ([9d5c824](https://github.com/afalfallaj/ha_gimdow_ble/commit/9d5c8243e8e9876b587169033e82826b7a6c19ca))
* Generalize Gimdow lock behavior from specific product ID to all lock products. ([8ef506b](https://github.com/afalfallaj/ha_gimdow_ble/commit/8ef506b5c31e152b971e1314c9a1be1369723c51))
* Gimdow BLE connection and pairing logic, update coordinator data, and refine exception handling. ([daebbb2](https://github.com/afalfallaj/ha_gimdow_ble/commit/daebbb2b8d21d5e0ea6bb583957746f4df1c9d0e))
* improve transition state handling in lock_manager ([f19d835](https://github.com/afalfallaj/ha_gimdow_ble/commit/f19d835e501b3e9617d4178123338f5b70ad1dd3))
* Overhaul Gimdow BLE core for enhanced stability, reliability, and robust lock state management with new configuration options. ([f452da2](https://github.com/afalfallaj/ha_gimdow_ble/commit/f452da2022dd6b92bc0485013c9b7df237990b8f))
* Populate Bluetooth adapter list from discovered service sources instead of scanner names. ([2f3d09d](https://github.com/afalfallaj/ha_gimdow_ble/commit/2f3d09da1f08c62f49291d2fc64276348dbd8c30))
* Relocate import of various constants from homeassistant.const to local consts. ([0b87ea1](https://github.com/afalfallaj/ha_gimdow_ble/commit/0b87ea15e6a077c10b3d957676c4b84f94a6abc7))
* Remove disconnect and sleep before sending the second unlock command. ([41a173f](https://github.com/afalfallaj/ha_gimdow_ble/commit/41a173f1b852b5ba78c6517502d368b67f182a73))
* remove redundant notification retry logic and add project d… ([fffd66a](https://github.com/afalfallaj/ha_gimdow_ble/commit/fffd66a274ab142966402ea969129c15f854bd2c))
* remove redundant notification retry logic and add project documentation ([299d436](https://github.com/afalfallaj/ha_gimdow_ble/commit/299d43679c3e773651db1f2df5b58be28b2adae6))
* update release workflows ([fcd9c7f](https://github.com/afalfallaj/ha_gimdow_ble/commit/fcd9c7f70b1e15e2b6a2c1977a201e348a80570e))
* Use direct dictionary access instead of `.get()` for datapoint retrieval. ([894271d](https://github.com/afalfallaj/ha_gimdow_ble/commit/894271d7a93702ecda52608a96c665ba57b07062))


### ### Documentation

* Remove `changes.md` release notes document. ([7c22158](https://github.com/afalfallaj/ha_gimdow_ble/commit/7c22158ef071ac8c1cf69719b79f68ded41c6ad7))

## [Unreleased]

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
