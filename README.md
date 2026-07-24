# Gimdow A1 Pro Max BLE

[![HACS][hacs-badge]][hacs-url]
[![GitHub Release][release-badge]][release-url]
[![License][license-badge]](LICENSE)

**Local Bluetooth (BLE) integration for the Gimdow A1 Pro Max smart lock in Home Assistant.**

No cloud required for day-to-day operation. Tuya Cloud credentials are only used during initial setup to retrieve your device keys.

---

## Contents

- [Features](#features)
- [Supported Devices](#supported-devices)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Setup](#setup)
- [Configuration](#configuration)
- [Entities](#entities)
- [Troubleshooting](#troubleshooting)
- [Gimdow A1 Known Limitations](#gimdow-a1-known-limitations)
- [Contributing](#contributing)
- [Disclaimer](#disclaimer)

---

## Features

- **Lock / Unlock** — full control from Home Assistant, automations, and voice assistants.
- **Real-time state** — lock position pushed over BLE; no polling.
- **Battery state** — enum sensor reporting `high`, `normal`, `low`, or `poweroff`.
- **Door sensor integration** — pair any `binary_sensor` for door-aware safety logic.
  - **Safety interlock**: prevents the bolt from extending while the door is open ("Jammed" state).
  - **Auto-lock on close**: if a lock command was blocked by an open door, the lock engages the moment the door shuts.
- **Virtual auto-lock** — configurable countdown timer that re-locks after unlock, with door-sensor awareness.
- **Unknown-state recovery** — three selectable strategies for recovering lock position after a BLE reconnect.
- **Calibration buttons** — Sync Clock, Recalibrate, Unlock More, Keep Retracted, Add Force.
- **ESPHome Bluetooth proxy** compatible.

---

## Supported Devices

| Device | Product ID | Status |
|--------|-----------|--------|
| Gimdow A1 Pro Max | `rlyxv7pe` | ✅ Supported |

Other Gimdow models that use the Tuya BLE protocol may work but are untested.

---

## Prerequisites

- Home Assistant **2024.3** or later.
- A Bluetooth adapter or [ESPHome Bluetooth proxy](https://esphome.io/components/bluetooth_proxy.html) within range of the lock.
  - **Important for ESPHome users**: If you are using ESP supports BLE 5.you must force the Bluetooth stack to use BLE 4.2, as the lock hardware may reject BLE 5.0 connections (status 133 / Cmd Disallowed). Add this to your ESPHome YAML:
    ```yaml
    esp32:
      board: esp32-c6-devkitc-1
      framework:
        type: esp-idf
        sdkconfig_options:
          # Explicitly enable BLE 4.2 support
          CONFIG_BT_BLE_42_FEATURES_SUPPORTED: "y"
          # Disable BLE 5.0 features (forces fallback to BLE 4.2 mode)
          CONFIG_BT_BLE_50_FEATURES_SUPPORTED: "n"
    ```
- For automatic setup: a [Tuya IoT Platform](https://iot.tuya.com/) account linked to the app where the lock was registered. See the [official Tuya integration guide](https://www.home-assistant.io/integrations/tuya/) for credential instructions.

---

## Installation

### HACS (recommended)

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=afalfallaj&repository=ha_gimdow_ble&category=integration)

1. Click the button above, or add `https://github.com/afalfallaj/ha_gimdow_ble` as a custom HACS repository (type: Integration).
2. Search for **Gimdow A1 Pro Max BLE** and install it.
3. Restart Home Assistant.

### Manual

1. Download the [latest release](https://github.com/afalfallaj/ha_gimdow_ble/releases/latest).
2. Copy the `custom_components/gimdow_ble` folder into your `config/custom_components/` directory.
3. Restart Home Assistant.

---

## Setup

Go to **Settings → Devices & Services → Add Integration** and search for **Gimdow A1 Pro Max BLE**.

Two setup methods are available:

### Automatic (Tuya Cloud)

Recommended if you do not have your device keys.

1. Select **Login via Tuya Cloud**.
2. Fill in your Tuya IoT credentials (country, Access ID, Access Secret, username, password).
3. *(Optional)* Select a `binary_sensor` as the door contact for the lock.
4. Choose your device from the discovered list.

### Manual

Use this if you already have your UUID, local key, and device ID.

1. Select **Manual Entry (Advanced)**.
2. Choose the device MAC address from the discovered list.
3. Enter:
   - **UUID**
   - **Local Key**
   - **Device ID**
   - **Device Name** (optional label)

---

## Configuration

After setup, go to **Settings → Devices & Services → Gimdow A1 Pro Max BLE → Configure** to adjust the following options.

### Door Sensor

Attach or change the optional `binary_sensor` that represents the door contact. When set:

- The lock will refuse to bolt while the door is open (Jammed state).
- A pending lock intent is fulfilled automatically when the door closes.

### Bluetooth Adapter

Select a specific Bluetooth adapter or proxy to use for this device. Useful when multiple adapters are present.

### Virtual Auto-Lock

Automatically re-locks the door after a configurable delay following an unlock. **A door sensor must be configured** (Options → Door Sensor) for the auto-lock timer to function. Without a door sensor, the countdown will not start and a warning will appear in the HA notification panel.

| Door Sensor | Scenario | Behaviour |
|-------------|----------|-----------|
| **Required** | Not configured | Auto-lock timer is disabled. A persistent notification will alert you. |
| Enabled | Unlocked, door closed | Countdown starts; re-locks after the configured delay. |
| Enabled | Unlocked, door left open | Countdown runs. If the timer fires while the door is still open, lock enters **Jammed** state (open-door alert). Re-locks instantly when the door closes. |
| Enabled | Manually locked while open | Lock enters **Jammed** state. Re-locks instantly when the door closes. |

The auto-lock delay is set via the **Auto-Lock Delay** number entity (in seconds).

### Unknown State Strategy

Determines how the integration recovers lock position after a BLE reconnect or restart, before the device reports its current state.

> **Why does state show Unknown after reconnect?**
> The lock's state datapoint (DP47) is only pushed when the motor physically moves. On every BLE
> reconnect the integration clears the stale pre-disconnect value and waits for a fresh reading.
> Manual physical operation of the lock while BLE is connected pushes DP47 immediately (~5–9 s)
> and is tracked correctly. The limitation is reconnect-only.

| Strategy | Trigger | Behaviour |
|----------|---------|-----------|
| **Confirm Last** *(default)* | Every BLE reconnect | Re-sends the last known state (locked or unlocked) twice, then waits for a single device acknowledgement. Worst-case ~22 s. If no prior state is recorded, stays Unknown until the device reports. |
| **Double on Action** | When a lock/unlock command is issued while state is Unknown | Sends the requested command twice with a single echo wait. Use when you prefer not to touch the lock automatically on reconnect. |
| **Force Lock Twice** | Every BLE reconnect | Sends the lock command twice unconditionally, then waits for echo. Use only when the lock is never in a position where extending the bolt is unsafe. |

> **Confirm Last** and **Force Lock Twice** fire automatically on every reconnect.
> **Double on Action** fires only when you issue a lock or unlock command from HA while state is Unknown.

### State Transition Timeout

Maximum time (seconds) the integration waits for a lock or unlock transition to be confirmed by the device before falling back to an Unknown state. Default: **60 s**. Set to **0** to disable.

---

## Entities

| Entity | Type | Description |
|--------|------|-------------|
| Lock | `lock` | Main lock control and state. |
| Battery State | `sensor` | Battery state: `high`, `normal`, `low`, `poweroff`. |
| Signal Strength | `sensor` | BLE RSSI in dBm. |
| Auto-Lock Delay | `number` | Countdown timer for virtual auto-lock (seconds). |
| Lock Volume | `select` | Motor sound level. |
| Motor Direction | `switch` | Clockwise / counter-clockwise bolt direction. |
| Sync Clock | `button` | Synchronise the lock's internal clock with HA. |
| Recalibrate | `button` | Run mechanical calibration. |
| Unlock More | `button` | Extend the unlock travel for better clearance. |
| Keep Retracted | `button` | Hold the bolt fully retracted. |
| Add Force | `button` | Increase motor torque for stiff installations. |

---

## Gimdow A1 Known Limitations

Based on Gimdow A1 Pro Max hardware-level constraints concluded from direct BLE testing, the following behaviors have been worked around in the integration:

**Lock state is unknown after every reconnect.**
The device only reports its position (DP47) when the motor physically moves; it never pushes the current state upon reconnection. Every time the BLE session re-establishes, the lock state remains unknown until the motor runs. The integration's reconnect strategies (Confirm Last, Force Lock Twice) resolve this limitation.

**Concurrent commands are silently dropped.**
If a second command arrives while the motor is running, the device ignores it. The integration handles this gracefully by waiting for confirmation before sending the next command.

**Lock position cannot be polled.**
There is no way to read the current bolt position on demand. The device only sends a position report when the motor completes a movement. Battery, signal, and other sensors can be refreshed on demand, but the lock state cannot.

---

## Contributing

Bug reports and pull requests are welcome on [GitHub](https://github.com/afalfallaj/ha_gimdow_ble/issues). Please include Home Assistant logs (with debug logging enabled for `custom_components.gimdow_ble`) when reporting connectivity issues.

To enable debug logging add to `configuration.yaml`:

```yaml
logger:
  default: warning
  logs:
    custom_components.gimdow_ble: debug
```

---

## Disclaimer

This is an **unofficial** community integration and is not affiliated with or endorsed by Gimdow or Tuya. It is provided "as is" without warranty of any kind.

---

*Built on the work of [@airy10](https://github.com/airy10) and [@redphx](https://github.com/redphx).*

<!-- Badges -->
[hacs-badge]: https://img.shields.io/badge/HACS-Custom-orange.svg
[hacs-url]: https://github.com/hacs/integration
[release-badge]: https://img.shields.io/github/v/release/afalfallaj/ha_gimdow_ble
[release-url]: https://github.com/afalfallaj/ha_gimdow_ble/releases
[license-badge]: https://img.shields.io/github/license/afalfallaj/ha_gimdow_ble
