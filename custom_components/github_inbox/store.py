"""The inbox's persisted state: open items, what was seen, the activity log and recent delivery ids."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN

STORAGE_VERSION = 1
SAVE_DELAY = 5


def empty() -> dict[str, Any]:
    return {
        "items": {},
        "seen": {},
        "activity": [],
        "deliveries": [],
        "repos": [],
        "last_sync": None,
        "last_delivery": None,
    }


class InboxStore:
    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}"
        )
        self.data = empty()

    async def async_load(self) -> None:
        stored = await self._store.async_load()
        data = empty()
        if isinstance(stored, dict):
            data.update({k: v for k, v in stored.items() if k in data})
        self.data = data

    def async_save(self) -> None:
        self._store.async_delay_save(lambda: self.data, SAVE_DELAY)

    async def async_flush(self) -> None:
        await self._store.async_save(self.data)

    async def async_remove(self) -> None:
        await self._store.async_remove()
