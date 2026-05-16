"""Tests for cloud.py — HASSGimdowBLEDeviceManager init and cache lock."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from custom_components.gimdow_ble.cloud import HASSGimdowBLEDeviceManager


class TestHASSManagerInit:
    def test_none_hass_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="hass must not be None"):
            HASSGimdowBLEDeviceManager(None, {})

    def test_valid_hass_creates_instance(self) -> None:
        hass = MagicMock()
        hass.data = {}
        manager = HASSGimdowBLEDeviceManager(hass, {"key": "value"})
        assert manager._hass is hass
        assert manager._data == {"key": "value"}


class TestCacheLock:
    def test_cloud_lock_returns_asyncio_lock(self) -> None:
        """The per-hass cache lock must be an asyncio.Lock."""
        hass = MagicMock()
        hass.data = {}
        manager = HASSGimdowBLEDeviceManager(hass, {})
        lock = manager._cloud_lock()
        assert isinstance(lock, asyncio.Lock)

    def test_cloud_lock_returns_same_instance(self) -> None:
        """Two calls must return the same Lock instance (singleton per hass)."""
        hass = MagicMock()
        hass.data = {}
        manager = HASSGimdowBLEDeviceManager(hass, {})
        lock1 = manager._cloud_lock()
        lock2 = manager._cloud_lock()
        assert lock1 is lock2

    async def test_cloud_lock_is_reentrant_safe(self) -> None:
        """Two concurrent tasks acquiring the lock must not deadlock."""
        hass = MagicMock()
        hass.data = {}
        manager = HASSGimdowBLEDeviceManager(hass, {})
        results = []

        async def task(tag: str) -> None:
            async with manager._cloud_lock():
                results.append(f"enter:{tag}")
                await asyncio.sleep(0)
                results.append(f"exit:{tag}")

        await asyncio.gather(task("A"), task("B"))
        assert len(results) == 4
        assert results.index("exit:A") < results.index("enter:B") or results.index(
            "exit:B"
        ) < results.index("enter:A")

    def test_cloud_cache_dict_exists(self) -> None:
        """The per-hass cache must be a dict accessible via _cloud_cache()."""
        hass = MagicMock()
        hass.data = {}
        manager = HASSGimdowBLEDeviceManager(hass, {})
        cache = manager._cloud_cache()
        assert isinstance(cache, dict)
