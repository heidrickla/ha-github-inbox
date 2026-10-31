"""Diagnostics: the entry with its key, secret and webhook redacted, and counts; no titles or bodies."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import model
from .const import (
    CONF_NOTIFY_TARGETS,
    CONF_PRIVATE_KEY,
    CONF_WEBHOOK_ID,
    CONF_WEBHOOK_SECRET,
)
from .coordinator import InboxConfigEntry

TO_REDACT = {CONF_PRIVATE_KEY, CONF_WEBHOOK_SECRET, CONF_WEBHOOK_ID}
# Notify service names carry device names.
OPTIONS_TO_REDACT = {CONF_NOTIFY_TARGETS}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: InboxConfigEntry
) -> dict[str, Any]:
    coordinator = entry.runtime_data
    stored = coordinator.store.data
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": async_redact_data(dict(entry.options), OPTIONS_TO_REDACT),
        },
        "sync": {
            "state": coordinator.sync_state,
            "error": coordinator.sync_error,
            "installation_lost": coordinator.installation_lost,
            "last_sync": stored["last_sync"],
            "last_delivery": stored["last_delivery"],
            "last_update_success": coordinator.last_update_success,
        },
        "counts": {
            **{k: v for k, v in coordinator.data.items() if k != "last_delivery"},
            "repositories": len(stored["repos"]),
            "items": len(stored["items"]),
            "activity": len(stored["activity"]),
            "deliveries_remembered": len(stored["deliveries"]),
            "ci": {
                state: sum(
                    model.ci_state(value["workflows"]) == state
                    for value in stored["ci"].values()
                )
                for state in (
                    "failing",
                    "running",
                    "passing",
                    "no_ci",
                    "cancelled",
                    "action_required",
                )
            },
            "ci_unreadable": sum(
                bool(value.get("unreadable")) for value in stored["ci"].values()
            ),
        },
    }
