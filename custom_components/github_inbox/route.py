"""How GitHub reaches Home Assistant: a Home Assistant Cloud cloudhook, or the external URL.

The cloud integration is imported inside each function: importing it loads Assist and its dependencies, and it is
only needed where Home Assistant Cloud is set up.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from homeassistant.components import webhook
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.network import NoURLAvailableError

from .const import ROUTE_CLOUD, ROUTE_EXTERNAL

_LOGGER = logging.getLogger(__name__)


class RouteUnavailableError(Exception):
    """The chosen route cannot carry webhooks now."""


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
        from homeassistant.components import cloud

        try:
            return await cloud.async_get_or_create_cloudhook(hass, webhook_id)
        except HomeAssistantError as err:
            raise RouteUnavailableError(route) from err
    url = external_url(hass, webhook_id)
    if url is None:
        raise RouteUnavailableError(route)
    return url


async def async_delete_cloudhook(hass: HomeAssistant, webhook_id: str) -> None:
    if "cloud" not in hass.config.components:
        return
    from homeassistant.components import cloud

    try:
        await cloud.async_delete_cloudhook(hass, webhook_id)
    except (HomeAssistantError, ValueError) as err:
        _LOGGER.debug("No cloudhook removed for this inbox: %s", err)


def listen_cloud(
    hass: HomeAssistant, on_connected: Callable[[], None]
) -> CALLBACK_TYPE | None:
    """Call `on_connected` each time the cloud connection comes up."""
    if "cloud" not in hass.config.components:
        return None
    from homeassistant.components import cloud

    def changed(state: cloud.CloudConnectionState) -> None:
        if state is cloud.CloudConnectionState.CLOUD_CONNECTED:
            on_connected()

    return cloud.async_listen_connection_change(hass, changed)
