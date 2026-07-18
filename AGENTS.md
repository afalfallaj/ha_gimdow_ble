# AGENTS.md — AI Agent Guide

## Project

Home Assistant custom integration for the **Gimdow A1 Pro Max BLE** smart lock.
- HA integration root: `custom_components/gimdow_ble/`
- BLE sub-package: `custom_components/gimdow_ble/gimdow_ble/`

---

## Versioning — Rules for AI Agents

### Never do these
- **Never manually edit version strings** in any file.
- **Never create git tags** — release-please owns all tags.
- **Never write changelog entries** to `CHANGELOG.md` — it is auto-generated.

### Version lives in three places — all managed automatically

| File | Mechanism |
|---|---|
| `.release-please-manifest.json` → `"."` | release-please tracking for **main** (stable versions only) |
| `.release-please-manifest-dev.json` → `"."` | release-please tracking for **dev** (beta versions only) |
| `custom_components/gimdow_ble/manifest.json` → `"version"` | release-please `extra-files` JSON path |
| `custom_components/gimdow_ble/gimdow_ble/__init__.py` → `__version__ = "x.y.z"  # x-release-please-version` | release-please `extra-files` generic marker |

When release-please merges a Release PR it updates all tracked files in one commit. No manual edits are ever needed.

The two manifest files are intentionally separate so that beta version strings from `dev` never bleed into `main`'s release computation.

---

## Commit Message Format — Required

All commits **must** use [Conventional Commits](https://www.conventionalcommits.org/). release-please reads these to determine the next version and generate the CHANGELOG.

```
<type>(<optional scope>): <short description>
```

### Types and their effect

| Commit prefix | Version bump | In CHANGELOG |
|---|---|---|
| `feat:` | minor — `2.0.x` → `2.1.0` | Yes |
| `fix:` | patch — `2.0.5` → `2.0.6` | Yes |
| `refactor:` | patch | Yes |
| `perf:` | patch | Yes |
| `docs:` | none | Yes |
| `chore:` | none | Hidden |
| `test:` | none | Hidden |
| `ci:` | none | Hidden |
| `feat!:` or `BREAKING CHANGE:` footer | **major** | Yes |

### Examples

```
feat(lock): add fingerprint credential sync
fix(ble): prevent connection drop on ESPHome proxy handoff
refactor(cloud): extract credential caching into CloudCache class
feat!: rename strategy API

BREAKING CHANGE: force_lock renamed to force_lock_twice.
```

---

## Release Flow

### Beta (on `dev`)
1. Push conventional commits to `dev`
2. release-please automatically opens/updates a Release PR
3. Merge the Release PR → `manifest.json`, `__init__.py`, `.release-please-manifest.json` updated, tag `vX.Y.Z-beta.N` pushed, GitHub pre-release created, `CHANGELOG.md` updated

### Stable (on `main`)
1. Open PR `dev → main`, use **squash and merge**
   - Write the squash commit title as a conventional commit, e.g.:
     - `feat!: ...` → major bump (e.g. `2.x.x` → `3.0.0`)
     - `feat: ...` → minor bump (e.g. `2.0.5` → `2.1.0`)
     - `fix: ...` → patch bump
   - This is the commit release-please reads to determine the next stable version
   - `.release-please-manifest.json` is **not** touched by dev (separate manifest files), so no conflict resolution needed
2. release-please opens a Release PR on `main`
3. Merge it → stable tag `vX.Y.Z`, GitHub Release published
4. CI automatically resets `.release-please-manifest-dev.json` on `dev` to the new stable version — dev's next beta cycle starts fresh from that base (e.g. `3.0.0` → `3.1.0-beta.0` on the next `feat:` commit)

### Version format

| Branch | Format | Example |
|---|---|---|
| `dev` | semver pre-release (`beta.0`, `beta.1`, …) | `3.0.0-beta.0`, `3.0.0-beta.1` |
| `main` | semver stable | `3.0.0` |

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
| `.github/workflows/release-please.yml` | Runs release-please on push to `main` or `dev` |
| `.github/workflows/validate.yml` | Runs `hassfest` and HACS validation (`hacs/action`) on push, PR, a daily schedule, and manual dispatch |
| `.github/release-please-config.json` | Stable release config (main) |
| `.github/release-please-config-dev.json` | Pre-release config (dev): `versioning: prerelease`, `prerelease-type: beta.0` |
| `.release-please-manifest.json` | Tracks last **stable** version released from `main` — do not commit beta version strings here |
| `.release-please-manifest-dev.json` | Tracks last **beta** version released from `dev` — managed entirely by release-please on dev |
| `CHANGELOG.md` | Auto-updated by release-please (`changelog-path`) — do not edit manually |

`hacs.json`'s `"homeassistant"` minimum-version floor is **manually maintained** — release-please and the CI workflows above never touch it. Bump it whenever a change starts relying on a newer HA API (e.g. it was raised to `2025.8.0` because `config_flow.py` uses `OptionsFlowWithReload`, added in that HA release). A stale floor doesn't fail CI; it just lets HACS install the integration onto HA versions where an import silently breaks.
