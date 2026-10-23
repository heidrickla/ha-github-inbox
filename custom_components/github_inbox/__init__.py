"""GitHub Inbox: open issues and pull requests across your repositories, pushed by a GitHub App's webhook."""

from __future__ import annotations

from homeassistant.const import EVENT_CORE_CONFIG_UPDATE, Platform
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.typing import ConfigType

from . import panel, route, services, views, webhook, websocket
from .api import GitHubApp, GitHubAuthError, GitHubError, InstallationClient
from .const import (
    CONF_APP_ID,
    CONF_CLOUDHOOK,
    CONF_INSTALLATION_ID,
    CONF_PRIVATE_KEY,
    CONF_WEBHOOK_ID,
    DOMAIN,
    ISSUE_WEBHOOK_ROUTE,
    SIGNAL_UPDATED,
)
from .coordinator import InboxConfigEntry, InboxCoordinator
from .store import InboxStore

PLATFORMS = [Platform.EVENT, Platform.SENSOR]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Actions, panel commands and the flow's pages exist whether or not an entry is loaded."""
    services.async_setup_services(hass)
    websocket.async_setup(hass)
    views.async_setup(hass)
    await panel.async_setup_static(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: InboxConfigEntry) -> bool:
    app = GitHubApp(
        async_get_clientsession(hass),
        int(entry.data[CONF_APP_ID]),
        entry.data[CONF_PRIVATE_KEY],
    )
    client = InstallationClient(app, int(entry.data[CONF_INSTALLATION_ID]))
    try:
        await client.token()
    except GitHubAuthError as err:
        raise ConfigEntryAuthFailed(
            translation_domain=DOMAIN, translation_key="auth_failed"
        ) from err
    except GitHubError as err:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="cannot_connect",
            translation_placeholders={"error": str(err)},
        ) from err

    store = InboxStore(hass, entry.entry_id)
    await store.async_load()
    coordinator = InboxCoordinator(hass, entry, client, store)
    entry.runtime_data = coordinator

    try:
        webhook.async_register(hass, entry)
    except ValueError as err:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN, translation_key="webhook_taken"
        ) from err
    entry.async_on_unload(lambda: webhook.async_unregister(hass, entry))

    @callback
    def cloud_changed(connected: bool) -> None:
        _check_route(hass, entry)
        if connected:
            # Deliveries made while the cloud was down are lost to GitHub's log; a sync recovers them.
            coordinator.schedule_sync()

    @callback
    def config_changed(_event: Event) -> None:
        _check_route(hass, entry)

    if entry.data.get(CONF_CLOUDHOOK):
        unsub = route.listen_cloud(hass, cloud_changed)
        if unsub is not None:
            entry.async_on_unload(unsub)
    else:
        entry.async_on_unload(
            hass.bus.async_listen(EVENT_CORE_CONFIG_UPDATE, config_changed)
        )
    _check_route(hass, entry)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await panel.async_register(hass)
    entry.async_create_background_task(
        hass, coordinator.async_refresh(), f"{DOMAIN} first sync"
    )
    return True


@callback
def _check_route(hass: HomeAssistant, entry: InboxConfigEntry) -> None:
    """A repair while the chosen route cannot carry deliveries."""
    issue_id = f"{ISSUE_WEBHOOK_ROUTE}_{entry.entry_id}"
    if entry.data.get(CONF_CLOUDHOOK):
        ok = route.cloud_signed_in(hass)
    else:
        ok = route.external_url(hass, entry.data[CONF_WEBHOOK_ID]) is not None
    if ok:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_WEBHOOK_ROUTE,
        translation_placeholders={"account": entry.title},
    )


async def async_unload_entry(hass: HomeAssistant, entry: InboxConfigEntry) -> bool:
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    coordinator: InboxCoordinator = entry.runtime_data
    # Timers and refreshes stop before the last write, so none follows it.
    await coordinator.async_shutdown()
    await coordinator.store.async_flush()
    if not hass.config_entries.async_loaded_entries(DOMAIN):
        panel.async_remove(hass)
    async_dispatcher_send(hass, SIGNAL_UPDATED)
    return True


async def async_remove_entry(hass: HomeAssistant, entry: InboxConfigEntry) -> None:
    """Drop the cloudhook, the stored inbox and any repair. The GitHub App stays on GitHub."""
    if entry.data.get(CONF_CLOUDHOOK):
        await route.async_delete_cloudhook(hass, entry.data[CONF_WEBHOOK_ID])
    await InboxStore(hass, entry.entry_id).async_remove()
    ir.async_delete_issue(hass, DOMAIN, f"{ISSUE_WEBHOOK_ROUTE}_{entry.entry_id}")
