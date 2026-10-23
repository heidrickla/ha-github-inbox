"""An event entity that fires for every activity in the inbox, for automations."""

from __future__ import annotations

from typing import Any, override

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import ACTIVITY_TYPES
from .coordinator import InboxConfigEntry, InboxCoordinator
from .entity import InboxEntity

PARALLEL_UPDATES = 0
ATTRIBUTES = ("repo", "number", "kind", "title", "url", "actor", "bot", "own", "state")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: InboxConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([ActivityEvent(entry.runtime_data)])


class ActivityEvent(InboxEntity, EventEntity):
    _attr_translation_key = "activity"

    def __init__(self, coordinator: InboxCoordinator) -> None:
        super().__init__(coordinator, "activity")
        self._attr_event_types = list(ACTIVITY_TYPES)

    @override
    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.async_add_activity_listener(self._on_activity)
        )

    @callback
    def _on_activity(self, entry: dict[str, Any]) -> None:
        self._trigger_event(str(entry["type"]), {k: entry.get(k) for k in ATTRIBUTES})
        self.async_write_ha_state()
