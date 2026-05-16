# Tests

Two-tier test structure: **unit tests** (CI-safe, no hardware) and **integration tests** (hardware required, run manually).

---

## Unit Tests — `tests/`

Standard pytest suite. Run on every CI push; no BLE hardware needed.

```bash
pytest
```
---

## Integration Tests — `tests/integration/`

Hardware validation tool for contributors. **Not collected by pytest** (`norecursedirs = tests/integration` in `pytest.ini`). Requires a real Gimdow BLE lock and a BLE adapter.

See [tests/integration/README.md](integration/README.md) for full setup and usage instructions.