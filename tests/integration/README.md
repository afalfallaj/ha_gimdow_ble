# Hardware Integration Tests

Interactive scenarios that run against a real Gimdow BLE lock.
**These are not collected by pytest** — run them manually with a physical device.

## Prerequisites

- BLE adapter (macOS built-in / Linux `hci0` / Windows Bluetooth)
- Python 3.11+
- Gimdow lock powered on and within BLE range
- Credentials in `.env` (copy from `.env.example`)

## Setup

```bash
cp tests/integration/.env.example tests/integration/.env
# open .env and fill in your credentials
```

Install dependencies:

```bash
pip install bleak bleak-retry-connector tuya-iot-py-sdk pycryptodome python-dotenv
```

Or with `uv`:

```bash
uv run --with bleak --with bleak-retry-connector --with tuya-iot-py-sdk \
       --with pycryptodome --with python-dotenv \
       python tests/integration/run.py
```

## Run

```bash
# Full suite — all scenarios (~15 min, includes manual steps)
python tests/integration/run.py

# Quick sanity check — lock and unlock only (~2 min)
python tests/integration/run.py --quick

# Specific scenarios
python tests/integration/run.py --scenarios S2,S3,S11

# List available options
python tests/integration/run.py --help
```

## Scenarios

| ID  | What it tests                          | Expected        | Manual? |
|-----|----------------------------------------|-----------------|---------|
| S0  | Connection stability (125s idle)       | PASS            | No      |
| S1  | Initial connect + state read           | PASS            | No      |
| S2  | BLE unlock command                     | PASS            | No      |
| S3  | BLE lock command                       | PASS            | No      |
| S4  | Manual action push (unlock + lock)     | PASS            | Yes     |
| S5  | Autonomous periodic push (DP9)         | PASS            | No      |
| S6  | Reconnect — state unchanged            | HARDWARE_LIMIT  | No      |
| S9  | Unknown state via double-command echo  | PASS            | No      |
| S10 | Back-to-back command serialization     | PASS            | No      |
| S11 | Post-reconnect state recovery          | PASS            | No      |
| S12 | DP47 response time distribution (3×)   | PASS            | No      |

**HARDWARE_LIMIT** — a known device limitation. The test verifies the integration
handles it correctly (shows `unknown` state instead of a wrong value). These scenarios
will always produce `HARDWARE_LIMIT` on Gimdow A1 hardware; that is the expected result.

## Known Device Limitations

Documented from hardware testing (`test-res.txt`):

- **DP47 is push-only** — lock state is only reported when the motor physically moves.
  It is never included in an `update()` response.
- **State unknown after reconnect** — device never pushes DP47 on reconnect.
  Lock state is `None` (unknown) every time BLE re-establishes. Integration resolves
  this via reconnect strategies (Confirm Last, Force Lock Twice) — tested in S11.
- **Concurrent commands silently dropped** — if a second command arrives while the motor
  is running, the device ignores it. Integration handles this by waiting for DP47
  confirmation before sending the next command (tested in S10).
- **Motor response window** — confirmed 12–18s, recommended `transition_timeout = 28s`.

## Adding a Scenario

1. Add `async def scenario_SXX(device, results, **ctx)` in `harness/scenarios.py`
2. Add an entry in the `META` dict at the top of `harness/scenarios.py`
3. Register it in the `_SCENARIOS` list in `harness/main.py`

Follow the pattern of existing scenarios: call `print_scenario_header()`, use
`assert_dp()` for BLE assertions, `user_gate()` for physical steps, and append
a `ScenarioResult` to `results`.
