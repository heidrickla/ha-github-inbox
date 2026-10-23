"""How GitHub reaches Home Assistant: a Home Assistant Cloud cloudhook, or the external URL.

The cloud integration is imported inside each function: importing it loads Assist and its dependencies, and it is
only needed where Home Assistant Cloud is set up.

Every cloudhook made here is recorded before it exists and forgotten once deleted. A restart drops a flow waiting on
GitHub without calling its `async_remove`, so the sweep deletes recorded cloudhooks that no entry on the cloud route
and no running flow holds.
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from homeassistant.components import webhook
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.network import NoURLAvailableError
from homeassistant.helpers.storage import Store
from homeassistant.util.hass_dict import HassKey

from .const import CONF_CLOUDHOOK, CONF_WEBHOOK_ID, DOMAIN, ROUTE_CLOUD, ROUTE_EXTERNAL

_LOGGER = logging.getLogger(__name__)


class RouteUnavailableError(Exception):
    """The chosen route cannot carry webhooks now."""


@dataclass(slots=True)
class Hooks:
    """The cloudhooks this integration made and has not deleted."""

    store: Store[dict[str, Any]]
    made: set[str]
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def async_save(self) -> None:
        await self.store.async_save({"made": sorted(self.made)})


DATA_HOOKS: HassKey[Hooks] = HassKey(f"{DOMAIN}_cloudhooks")
DATA_HELD: HassKey[Counter[str]] = HassKey(f"{DOMAIN}_held")


async def _hooks(hass: HomeAssistant) -> Hooks:
    if (hooks := hass.data.get(DATA_HOOKS)) is not None:
        return hooks
    store: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}.cloudhooks")
    stored = await store.async_load()
    if (hooks := hass.data.get(DATA_HOOKS)) is not None:
        return hooks
    made = stored.get("made") if isinstance(stored, dict) else None
    hooks = Hooks(store, {str(w) for w in made} if isinstance(made, list) else set())
    hass.data[DATA_HOOKS] = hooks
    return hooks


@callback
def hold(hass: HomeAssistant, webhook_id: str) -> None:
    """A running flow may still hand this webhook to GitHub, so the sweep leaves its cloudhook."""
    hass.data.setdefault(DATA_HELD, Counter())[webhook_id] += 1


@callback
def release(hass: HomeAssistant, webhook_id: str) -> None:
    held = hass.data.setdefault(DATA_HELD, Counter())
    held[webhook_id] -= 1
    if held[webhook_id] <= 0:
        del held[webhook_id]


def cloud_ready(hass: HomeAssistant) -> bool:
    """Signed in with an active subscription and connected."""
    if "cloud" not in hass.config.components:
        return False
    from homeassistant.components import cloud

    return cloud.async_active_subscription(hass) and cloud.async_is_connected(hass)


def cloud_signed_in(hass: HomeAssistant) -> bool:
    """Signed in with an active subscription, connected or not."""
    if "cloud" not in hass.config.components:
        return False
    from homeassistant.components import cloud

    return cloud.async_active_subscription(hass)


def external_url(hass: HomeAssistant, webhook_id: str) -> str | None:
    try:
        return webhook.async_generate_url(
            hass, webhook_id, allow_internal=False, prefer_external=True
        )
    except NoURLAvailableError:
        return None


def available_routes(hass: HomeAssistant) -> list[str]:
    routes = [ROUTE_CLOUD] if cloud_ready(hass) else []
    if external_url(hass, "probe") is not None:
        routes.append(ROUTE_EXTERNAL)
    return routes


async def async_hook_url(hass: HomeAssistant, webhook_id: str, route: str) -> str:
    """The URL GitHub delivers to on this route, creating the cloudhook if needed."""
    if route == ROUTE_CLOUD:
        if not cloud_ready(hass):
            raise RouteUnavailableError(route)
        from hass_nabucasa.exceptions import NabuCasaBaseError
        from homeassistant.components import cloud

        hooks = await _hooks(hass)
        async with hooks.lock:
            if webhook_id not in hooks.made:
                hooks.made.add(webhook_id)
                await hooks.async_save()
            try:
                return await cloud.async_get_or_create_cloudhook(hass, webhook_id)
            except (
                HomeAssistantError,
                NabuCasaBaseError,
                TimeoutError,
                ValueError,
            ) as err:
                # Nabu Casa's own errors, its timeout, and the connection dropping mid-call.
                raise RouteUnavailableError(route) from err
    url = external_url(hass, webhook_id)
    if url is None:
        raise RouteUnavailableError(route)
    return url


async def async_delete_cloudhook(hass: HomeAssistant, webhook_id: str) -> None:
    hooks = await _hooks(hass)
    async with hooks.lock:
        await _delete(hass, hooks, webhook_id)


async def _delete(hass: HomeAssistant, hooks: Hooks, webhook_id: str) -> None:
    """Delete one cloudhook and forget it; a failure keeps it recorded for the next sweep."""
    if "cloud" not in hass.config.components:
        return
    from hass_nabucasa.exceptions import NabuCasaBaseError
    from homeassistant.components import cloud

    try:
        await cloud.async_delete_cloudhook(hass, webhook_id)
    except ValueError:
        # Nabu Casa's answer for a cloudhook that is not there: nothing left to delete.
        pass
    except (HomeAssistantError, NabuCasaBaseError, TimeoutError) as err:
        _LOGGER.debug("No cloudhook removed for this inbox: %s", err)
        return
    if webhook_id in hooks.made:
        hooks.made.discard(webhook_id)
        await hooks.async_save()


async def async_sweep(hass: HomeAssistant) -> None:
    """Delete the recorded cloudhooks that no entry on the cloud route and no running flow holds."""
    if not cloud_ready(hass):
        return
    hooks = await _hooks(hass)
    async with hooks.lock:
        owned = {
            entry.data.get(CONF_WEBHOOK_ID)
            for entry in hass.config_entries.async_entries(DOMAIN)
            if entry.data.get(CONF_CLOUDHOOK)
        }
        held = hass.data.get(DATA_HELD) or Counter()
        for webhook_id in sorted(hooks.made - owned - set(held)):
            await _delete(hass, hooks, webhook_id)


@callback
def async_sweep_when_connected(hass: HomeAssistant) -> None:
    """Sweep now, or once the cloud connects; at startup it usually has not yet."""
    if cloud_ready(hass):
        hass.async_create_background_task(
            async_sweep(hass), f"{DOMAIN} cloudhook sweep"
        )
        return
    unsub: CALLBACK_TYPE | None = None

    @callback
    def changed(connected: bool) -> None:
        nonlocal unsub
        if not connected or unsub is None:
            return
        unsub()
        unsub = None
        hass.async_create_background_task(
            async_sweep(hass), f"{DOMAIN} cloudhook sweep"
        )

    unsub = listen_cloud(hass, changed)


def listen_cloud(
    hass: HomeAssistant, on_change: Callable[[bool], None]
) -> CALLBACK_TYPE | None:
    """Call `on_change(connected)` each time the cloud connection comes up or goes down."""
    if "cloud" not in hass.config.components:
        return None
    from homeassistant.components import cloud

    @callback
    def changed(state: cloud.CloudConnectionState) -> None:
        on_change(state is cloud.CloudConnectionState.CLOUD_CONNECTED)

    return cloud.async_listen_connection_change(hass, changed)
