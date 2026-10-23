"""The webhook GitHub delivers to, through a Home Assistant Cloud cloudhook or the external URL.

Every delivery is checked against the app's webhook secret (`X-Hub-Signature-256`, HMAC-SHA256 of the raw body)
before anything in it is read. The answer goes back at once and the inbox applies the event after.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from http import HTTPStatus

from aiohttp import web
from homeassistant.components import webhook
from homeassistant.core import HomeAssistant, callback

from .const import CONF_WEBHOOK_ID, CONF_WEBHOOK_SECRET, DOMAIN
from .coordinator import InboxConfigEntry


def signature_valid(secret: str, body: bytes, header: str | None) -> bool:
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


def _entry_for(hass: HomeAssistant, webhook_id: str) -> InboxConfigEntry | None:
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        if entry.data.get(CONF_WEBHOOK_ID) == webhook_id:
            return entry
    return None


async def _handle(
    hass: HomeAssistant, webhook_id: str, request: web.Request
) -> web.Response:
    entry = _entry_for(hass, webhook_id)
    if entry is None:
        return web.Response(status=HTTPStatus.NOT_FOUND)
    # text() works for aiohttp's request and for the cloud's relayed one; GitHub sends UTF-8 JSON.
    body = (await request.text()).encode()
    if not signature_valid(
        entry.data[CONF_WEBHOOK_SECRET],
        body,
        request.headers.get("X-Hub-Signature-256"),
    ):
        return web.Response(status=HTTPStatus.UNAUTHORIZED)
    try:
        payload = json.loads(body)
    except ValueError:
        return web.Response(status=HTTPStatus.BAD_REQUEST)
    if not isinstance(payload, dict):
        return web.Response(status=HTTPStatus.BAD_REQUEST)
    entry.runtime_data.async_receive(
        request.headers.get("X-GitHub-Event", ""),
        request.headers.get("X-GitHub-Delivery", ""),
        payload,
    )
    return web.Response(status=HTTPStatus.OK, text="ok")


@callback
def async_register(hass: HomeAssistant, entry: InboxConfigEntry) -> None:
    webhook.async_register(
        hass,
        DOMAIN,
        f"GitHub Inbox {entry.title}",
        entry.data[CONF_WEBHOOK_ID],
        _handle,
        allowed_methods=["POST"],
    )


@callback
def async_unregister(hass: HomeAssistant, entry: InboxConfigEntry) -> None:
    webhook.async_unregister(hass, entry.data[CONF_WEBHOOK_ID])
