"""The resync action: read every open item from GitHub again."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.service import async_register_admin_service

from .const import DOMAIN, SERVICE_RESYNC
from .coordinator import InboxCoordinator


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    async def resync(call: ServiceCall) -> None:
        entries = hass.config_entries.async_loaded_entries(DOMAIN)
        if not entries:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="not_loaded"
            )
        failed = []
        for entry in entries:
            coordinator: InboxCoordinator = entry.runtime_data
            await coordinator.async_refresh()
            if not coordinator.last_update_success:
                failed.append(entry.title)
        if failed:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="resync_failed",
                translation_placeholders={"accounts": ", ".join(failed)},
            )

    # A resync spends the installation's API budget and reads private repositories.
    async_register_admin_service(
        hass, DOMAIN, SERVICE_RESYNC, resync, schema=vol.Schema({})
    )
