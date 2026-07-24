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

## Hardware Findings (DP47 / Lock State)

These findings are confirmed by hardware test results (`test-res.txt`).

| Finding | Description |
|---|---|
| DP47 on manual operation | **DP47 IS pushed** when the user manually operates the lock while BLE is connected. Echo arrives in ~5–9 s depending on direction. State is tracked correctly without any special handling. |
| DP47 on reconnect | DP47 is **NOT** pushed by the device after a BLE reconnect. The integration clears the stale value on reconnect and relies on the configured unknown-state strategy to resolve position. |
| DP47 double-command timing | When two commands are sent (double-command pattern), the device pushes DP47 after the second motor operation completes. Observed echo times: ~8.7 s (lock) and ~17.3 s (unlock). Well within the 60 s default timeout. |
| No HA-state restore for lock position | `lock.py` deliberately does **not** restore last-known "locked"/"unlocked" from Home Assistant's entity state history (`RestoreEntity`) on startup. A restored value can go stale during any HA-down window (the lock can be operated manually while HA is off) and can't be corroborated by a fresh device read on reconnect, since DP47 isn't re-pushed (see above) — reasserting it via a double-command risks overwriting a legitimate manual operation. `GimdowBLELockManager._last_known_state` is seeded **exclusively** from live DP47 pushes via `on_coordinator_update()`; after a full HA restart it starts unknown, so `confirm_last`/`force_lock_twice` stay unknown until the device reports a real reading (they still resolve immediately from a live reconnect within an already-running session, once traffic has populated `_last_known_state`). **Do not reintroduce `RestoreEntity`/`async_get_last_state()`-based state restore in `lock.py`** — this was deliberately removed, not an oversight. |

---

## CI/CD Files

| File | Purpose |
|---|---|
| `.github/workflows/tag_and_release.yml` | Auto-generates tags, bumps manifests, and publishes GitHub Releases on push to `main` or `dev` |
| `.github/workflows/validate.yml` | Runs `hassfest` and HACS validation (`hacs/action`) on push, PR, a daily schedule, and manual dispatch |

`hacs.json`'s `"homeassistant"` minimum-version floor is **manually maintained** — release-please and the CI workflows above never touch it. Bump it whenever a change starts relying on a newer HA API (e.g. it was raised to `2025.8.0` because `config_flow.py` uses `OptionsFlowWithReload`, added in that HA release). A stale floor doesn't fail CI; it just lets HACS install the integration onto HA versions where an import silently breaks.
