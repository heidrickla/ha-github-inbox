"""The service device every inbox entity belongs to."""

from __future__ import annotations

from typing import override

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_APP_URL, DOMAIN
from .coordinator import InboxCoordinator


class InboxEntity(CoordinatorEntity[InboxCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: InboxCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.unique_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(entry.unique_id))},
            name=f"GitHub {entry.title}",
            manufacturer="GitHub",
            model="GitHub App",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=entry.data.get(CONF_APP_URL) or None,
        )

    @property
    @override
    def available(self) -> bool:
        """Unavailable after a failed sync, and while GitHub reports the app uninstalled or suspended."""
        return super().available and not self.coordinator.installation_lost
