"""Websocket commands for the panel: a subscription to the inbox, one item's bodies, seen marks and a resync."""

from __future__ import annotations

import datetime as dt
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.components.websocket_api.connection import ActiveConnection
from homeassistant.components.websocket_api.const import ERR_NOT_FOUND
from homeassistant.components.websocket_api.decorators import (
    require_admin,
    websocket_command,
)
from homeassistant.components.websocket_api.messages import event_message
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from . import model
from .const import DOMAIN, SIGNAL_UPDATED
from .coordinator import InboxCoordinator, item_detail

# Seconds a subscription waits after a change, so a burst of deliveries is one snapshot.
SNAPSHOT_DELAY = 0.25


def coordinators(hass: HomeAssistant) -> list[InboxCoordinator]:
    return [e.runtime_data for e in hass.config_entries.async_loaded_entries(DOMAIN)]


def build_snapshot(hass: HomeAssistant) -> dict[str, Any]:
    return model.snapshot([c.account_view() for c in coordinators(hass)], dt_util.now())


@callback
def async_setup(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_subscribe)
    websocket_api.async_register_command(hass, ws_item)
    websocket_api.async_register_command(hass, ws_mark_seen)
    websocket_api.async_register_command(hass, ws_resync)


@require_admin
@websocket_command({vol.Required("type"): f"{DOMAIN}/subscribe"})
@callback
def ws_subscribe(
    hass: HomeAssistant,
    connection: ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """A snapshot now and after every change."""
    pending: CALLBACK_TYPE | None = None

    @callback
    def send(_now: dt.datetime | None = None) -> None:
        nonlocal pending
        pending = None
        connection.send_message(
            event_message(msg["id"], {"snapshot": build_snapshot(hass)})
        )

    @callback
    def changed() -> None:
        nonlocal pending
        if pending is None:
            pending = async_call_later(hass, SNAPSHOT_DELAY, send)

    unsub = async_dispatcher_connect(hass, SIGNAL_UPDATED, changed)

    @callback
    def stop() -> None:
        unsub()
        if pending is not None:
            pending()

    connection.subscriptions[msg["id"]] = stop
    connection.send_result(msg["id"])
    send()


@require_admin
@websocket_command({vol.Required("type"): f"{DOMAIN}/item", vol.Required("key"): str})
@callback
def ws_item(
    hass: HomeAssistant,
    connection: ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """An item with its body and every comment and review."""
    for coordinator in coordinators(hass):
        item = coordinator.items.get(msg["key"])
        if item is not None:
            seen_at = coordinator.store.data["seen"].get(msg["key"], "")
            connection.send_result(msg["id"], {**item_detail(item), "seen_at": seen_at})
            return
    connection.send_error(msg["id"], ERR_NOT_FOUND, "No open item has that key")


@require_admin
@websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/mark_seen",
        vol.Exclusive("keys", "which"): [str],
        vol.Exclusive("all", "which"): True,
    }
)
@callback
def ws_mark_seen(
    hass: HomeAssistant,
    connection: ActiveConnection,
    msg: dict[str, Any],
) -> None:
    keys: list[str] | None = None if msg.get("all") else msg.get("keys", [])
    for coordinator in coordinators(hass):
        coordinator.async_mark_seen(keys)
    connection.send_result(msg["id"])


@require_admin
@websocket_command({vol.Required("type"): f"{DOMAIN}/resync"})
@callback
def ws_resync(
    hass: HomeAssistant,
    connection: ActiveConnection,
    msg: dict[str, Any],
) -> None:
    for coordinator in coordinators(hass):
        coordinator.schedule_sync()
    connection.send_result(msg["id"])
