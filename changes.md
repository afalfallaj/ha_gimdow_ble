# What's New in v2.0.0b1

This is a massive update! completely overhauled the inner workings of the Gimdow BLE integration to make it much more stable, reliable, and easier to maintain. The codebase is no longer just one giant file; it's now neatly organized into logical pieces under the hood. 

Here are the top highlights of what's changed and fixed:

## 🏗️ Better Architecture
- **Organized Code:** Split the massive monolithic code into clean, dedicated modules (like `connection`, `protocol`, `lock_manager`, etc.). This makes a world of difference for future updates and debugging.
- **Proper Lock Entities:** Your locks will now actually show up in Home Assistant under the proper `lock.*` domain instead of awkwardly pretending to be sensors.

## 🔴 Critical Fixes (No More Hanging!)
- **Fixed Infinite Reconnect Loops:** If a lock went permanently offline, the integration used to get stuck trying to reconnect forever, which could eat up your CPU. Added a cap (max 10 tries) and a sensible 5-minute backoff so it behaves nicely.
- **No More Double-Sends:** When a Bluetooth error happened, the integration would sometimes try to resend a command *and* throw an error at the same time, leading to weird race conditions. It now safely handles retries in the background.
- **Thread-Safety Crashes Squashed:** Fixed a nasty bug where an unexpected Bluetooth disconnect could crash the integration entirely because it was trying to run background tasks on the wrong thread.
- **Fixed the "Locking..." Spinner of Death:** Ever tried to force a lock and had it fail, leaving the HA dashboard spinning on "Locking..." forever? That’s fixed! It now properly resets the state if the command fails.

## 🟠 Smoother Lock Experience
- **Door-Open Warning:** If you try to lock the door while it's physically open (and auto-lock is on), the integration used to just silently ignore you. Now, it sets a "Jammed" state so you immediately know why it didn't lock.
- **Clearer Jamming State:** When everything is working normally, the lock will now confidently tell Home Assistant that it is *not* jammed (`False`), rather than shrugging with an "Unknown" state.
- **Safer Background Tasks:** Rewrote over a dozen background processes so that if they fail, they actually log an error instead of failing silently in the dark. 

## 🛠️ Under the Hood Polish
- **Tougher Protocol:** The integration is now much better at dealing with corrupted or weird Bluetooth data. Instead of crashing the whole connection, it will log an error and safely discard the bad data.
- **Logic Bug Squashing:** Fixed an operator precedence bug that was messing up cloud schema parsing, and cleaned up how the integration handles missing security keys.