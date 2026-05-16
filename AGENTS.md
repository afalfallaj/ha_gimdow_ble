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
| `custom_components/gimdow_ble/manifest.json` → `"version"` | release-please `extra-files` JSON path |
| `custom_components/gimdow_ble/gimdow_ble/__init__.py` → `__version__ = "x.y.z"  # x-release-please-version` | release-please `extra-files` generic marker |

When release-please merges a Release PR it updates all three in one commit. No manual edits are ever needed.

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
3. Merge the Release PR → `manifest.json`, `pyproject.toml`, `__init__.py` updated, tag `vX.Y.Z-beta.N` pushed, GitHub pre-release created

### Stable (on `main`)
1. Open PR `dev → main`, merge it — resolve `.release-please-manifest.json` conflict by keeping `main`'s value
2. release-please opens a Release PR on `main`
3. Merge it → stable tag `vX.Y.Z`, GitHub Release published

### Version format

| Branch | Format | Example |
|---|---|---|
| `dev` | semver pre-release | `2.1.0-beta.0`, `2.1.0-beta.1` |
| `main` | semver stable | `2.1.0` |

---

## Hardware Findings (DP47 / Lock State)

These findings are confirmed by hardware test results (`test-res.txt`).

| Finding | Description |
|---|---|
| DP47 on manual operation | **DP47 IS pushed** when the user manually operates the lock while BLE is connected. Echo arrives in ~5–9 s depending on direction. State is tracked correctly without any special handling. |
| DP47 on reconnect | DP47 is **NOT** pushed by the device after a BLE reconnect. The integration clears the stale value on reconnect and relies on the configured unknown-state strategy to resolve position. |
| DP47 double-command timing | When two commands are sent (double-command pattern), the device pushes DP47 after the second motor operation completes. Observed echo times: ~8.7 s (lock) and ~17.3 s (unlock). Well within the 60 s default timeout. |

---

## CI/CD Files

| File | Purpose |
|---|---|
| `.github/workflows/release-please.yml` | Runs release-please on push to `main` or `dev` |
| `.github/release-please-config.json` | Stable release config (main) |
| `.github/release-please-config-dev.json` | Pre-release config (dev), `prerelease: true` |
| `.release-please-manifest.json` | Branch-local; tracks last released version — differs between `main` and `dev` |
