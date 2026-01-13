# Gimdow A1 Pro Max BLE for Home Assistant

**Specialized Home Assistant integration for Gimdow A1 Pro Max Smart Locks (Model A1) via Bluetooth Low Energy (BLE).**

## Features

- **Lock Control**: Lock and Unlock your Gimdow device.
- **Status Reporting**: Real-time lock state and battery level.
- **Configuration**:
  - Auto-lock timer
  - Motor direction
  - Lock volume
   - Lock volume
- **Door Position Awareness (Optional)**:
  - Integrate with any `binary_sensor` (door contact) to make the lock "aware" of the door's position.
  - **Safety Interlock**: If you try to lock while the door is open, the lock enters a **"Jammed"** (Waiting) state instead of locking the bolt in mid-air.
  - **Auto-Lock on Close**: If a lock command was pending (Jammed state), the lock automatically engages the moment the door is closed.
- **Calibration**: Dedicated buttons for mechanical calibration:
  - Sync Clock
  - Recalibrate
  - Unlock More
  - Keep Retracted
  - Add Force

## Supported Devices

- **Gimdow A1 Pro Max** (Product ID: `rlyxv7pe`)
  - *Note: This integration is hardcoded for this specific device. Other Gimdow models may work but are not officially tested.*

## Installation

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=afalfallaj&repository=ha_gimdow_ble&category=integration)

### Option 1: HACS (Recommended)
1. Add this repository to HACS as a custom repository.
2. Search for "Gimdow A1 Pro Max BLE" and install.
3. Restart Home Assistant.

### Option 2: Manual
1. Copy the `custom_components/tuya_ble` folder to your `config/custom_components/` directory.
2. Restart Home Assistant.

## Setup

This integration supports two methods for setup: **Automatic (via Tuya Cloud)** and **Manual Entry**.

### Option 1: Automatic (Tuya Cloud)
Recommended if you don't have your device keys.

1. Go to **Settings > Devices & Services**.
2. Click **Add Integration** and search for **Gimdow A1 Pro Max BLE**.
3. Select **Login via Tuya Cloud**.
4. Enter your Tuya IoT credentials (Access ID, Access Secret, etc.).
   - *Refer to the [official Tuya integration documentation](https://www.home-assistant.io/integrations/tuya/) for instructions on how to get these credentials.*
   - **(Optional) Door Sensor**: You can select a binary sensor now or configure it later.
5. Select your Gimdow device from the discovered list.

### Option 2: Manual Configuration
Use this if you already have your device's keys and want to skip the cloud login.

1. Go to **Settings > Devices & Services**.
2. Click **Add Integration** and search for **Gimdow A1 Pro Max BLE**.
3. Select **Manual Entry (Advanced)**.
4. Enter the required device information:
   - **Device Address** (MAC address)
   - **UUID**
   - **Local Key**
   - **Device ID**

### Configuration
You can change sensor settings:
1. Go to **Settings > Devices & Services > Gimdow A1 Pro Max BLE**.
2. Click **Configure**.
3. **Door Sensor**: Add, change, or remove the optional door sensor to enable/disable the Door Position Awareness features.

## Usage

Once added, the following entities will be available:

- **Lock Details**: The main lock entity.
- **Buttons**: Use these to trigger specific calibration actions (e.g., if the lock isn't fully engaging, press "Unlock More" or "Add Force").
- **Numbers/Selects**: Adjust timeout settings and sound volume directly from Home Assistant.

## Troubleshooting

- **Delay**: Bluetooth Low Energy can have a slight delay. This is normal.
- **Range**: Ensure your Home Assistant server (or Bluetooth proxy) is within range of the lock.


## Disclaimer

This integration is an **unofficial** hobby project and is not affiliated with or supported by Gimdow or Tuya. It is provided "as is" without warranty of any kind.

---
*Based on the work of [@airy10](https://github.com/airy10) and [@redphx](https://github.com/redphx).*
