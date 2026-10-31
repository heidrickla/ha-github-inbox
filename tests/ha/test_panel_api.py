"""What the panel and automations reach: websocket commands, the resync action, diagnostics, the panel and the store."""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
from homeassistant.components import frontend
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import (
    HomeAssistantError,
    ServiceValidationError,
    Unauthorized,
)
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)

from custom_components.github_inbox import model
from custom_components.github_inbox.const import (
    DOMAIN,
    PANEL_URL,
    REFRESH_DELAY,
    SERVICE_RESYNC,
    STATIC_URL,
    VERSION,
)
from custom_components.github_inbox.coordinator import InboxCoordinator
from custom_components.github_inbox.websocket import SNAPSHOT_DELAY
from tests.ha.conftest import GitHub, setup_entry
from tests.payloads import REPO, comment, issue

KEY = f"{REPO}#2"


@pytest.fixture
async def inbox(
    hass: HomeAssistant, config_entry: MockConfigEntry, github: GitHub
) -> InboxCoordinator:
    github.token()
    github.repos([REPO])
    github.issues([issue(2, comments=1), issue(3)])
    github.comments(2, [comment(11)])
    return await setup_entry(hass, config_entry)


async def later(hass: HomeAssistant, seconds: float) -> None:
    async_fire_time_changed(hass, dt_util.utcnow() + dt.timedelta(seconds=seconds))
    await hass.async_block_till_done(wait_background_tasks=True)


# ------------------------------------------------------------- websocket


async def test_subscribe_sends_a_snapshot_now_and_once_per_burst(
    hass: HomeAssistant, inbox: InboxCoordinator, hass_ws_client: Any
) -> None:
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": f"{DOMAIN}/subscribe"})
    assert (await ws.receive_json())["success"]
    first = (await ws.receive_json())["event"]["snapshot"]
    assert first["totals"]["issues"] == 2
    assert first["totals"]["unseen"] == 2
    assert first["ci_totals"] == {"failing": 0, "running": 0, "passing": 0}
    assert first["ci"][0]["repo"] == REPO
    assert first["ci"][0]["workflows"] == []
    assert [s["state"] for s in first["sync"]] == ["ok"]
    assert {i["key"] for i in first["items"]} == {KEY, f"{REPO}#3"}

    inbox.async_mark_seen([KEY])
    inbox.async_mark_seen([f"{REPO}#3"])
    await later(hass, SNAPSHOT_DELAY + 0.1)
    second = (await ws.receive_json())["event"]["snapshot"]
    assert second["totals"]["unseen"] == 0

    await ws.send_json({"id": 2, "type": "unsubscribe_events", "subscription": 1})
    assert (await ws.receive_json())["success"]
    inbox.async_mark_seen(None)
    await later(hass, SNAPSHOT_DELAY + 0.1)
    await ws.send_json({"id": 3, "type": "ping"})
    assert (await ws.receive_json())["type"] == "pong"


async def test_an_unsubscribe_during_the_debounce_cancels_the_snapshot(
    hass: HomeAssistant, inbox: InboxCoordinator, hass_ws_client: Any
) -> None:
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": f"{DOMAIN}/subscribe"})
    await ws.receive_json()
    await ws.receive_json()
    inbox.async_mark_seen(None)
    await ws.send_json({"id": 2, "type": "unsubscribe_events", "subscription": 1})
    assert (await ws.receive_json())["success"]
    await later(hass, SNAPSHOT_DELAY + 0.1)
    await ws.send_json({"id": 3, "type": "ping"})
    assert (await ws.receive_json())["type"] == "pong"


async def test_item_returns_the_bodies_and_timeline(
    hass: HomeAssistant, inbox: InboxCoordinator, hass_ws_client: Any
) -> None:
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": f"{DOMAIN}/item", "key": KEY})
    result = (await ws.receive_json())["result"]
    assert result["body_html"] == "<p>Hi <strong>there</strong></p>"
    assert [e["id"] for e in result["timeline"]] == ["c11"]
    assert result["seen_at"] == ""
    assert "body_sig" not in result
    assert "body_sig" not in result["timeline"][0]
    await ws.send_json({"id": 2, "type": f"{DOMAIN}/item", "key": "nope#1"})
    reply = await ws.receive_json()
    assert reply["error"]["code"] == "not_found"


async def test_mark_seen_by_key_and_all(
    hass: HomeAssistant, inbox: InboxCoordinator, hass_ws_client: Any
) -> None:
    ws = await hass_ws_client(hass)
    await ws.send_json(
        {"id": 1, "type": f"{DOMAIN}/mark_seen", "keys": [KEY, "gone#9"]}
    )
    assert (await ws.receive_json())["success"]
    assert set(inbox.store.data["seen"]) == {KEY}
    assert hass.states.get("sensor.github_heidrickla_unseen").state == "1"
    await ws.send_json({"id": 2, "type": f"{DOMAIN}/mark_seen", "all": True})
    assert (await ws.receive_json())["success"]
    assert hass.states.get("sensor.github_heidrickla_unseen").state == "0"


async def test_marking_seen_holds_when_github_is_ahead_of_the_clock(
    hass: HomeAssistant, inbox: InboxCoordinator
) -> None:
    ahead = (dt_util.utcnow() + dt.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    inbox.items[KEY]["timeline"][-1]["at"] = ahead
    inbox.async_mark_seen([KEY])
    assert inbox.store.data["seen"][KEY] == ahead
    assert not model.unseen(inbox.items[KEY], inbox.store.data["seen"], inbox.me)


async def test_resync_command(
    hass: HomeAssistant, inbox: InboxCoordinator, hass_ws_client: Any, github: GitHub
) -> None:
    ws = await hass_ws_client(hass)
    before = github.calls("get", "/installation/repositories")
    await ws.send_json({"id": 1, "type": f"{DOMAIN}/resync"})
    assert (await ws.receive_json())["success"]
    await later(hass, REFRESH_DELAY + 1)
    assert github.calls("get", "/installation/repositories") == before + 1


@pytest.mark.parametrize(
    ("command", "extra"),
    [("subscribe", {}), ("resync", {}), ("mark_seen", {}), ("item", {"key": KEY})],
)
async def test_commands_are_for_administrators(
    hass: HomeAssistant,
    inbox: InboxCoordinator,
    hass_ws_client: Any,
    hass_read_only_access_token: str,
    command: str,
    extra: dict[str, Any],
) -> None:
    ws = await hass_ws_client(hass, hass_read_only_access_token)
    await ws.send_json({"id": 1, "type": f"{DOMAIN}/{command}", **extra})
    reply = await ws.receive_json()
    assert reply["error"]["code"] == "unauthorized"


# ----------------------------------------------------------------- action


async def test_resync_action_is_for_administrators(
    hass: HomeAssistant,
    inbox: InboxCoordinator,
    hass_admin_user: Any,
    hass_read_only_user: Any,
    github: GitHub,
) -> None:
    before = github.calls("get", "/installation/repositories")
    await hass.services.async_call(
        DOMAIN,
        SERVICE_RESYNC,
        {},
        blocking=True,
        context=Context(user_id=hass_admin_user.id),
    )
    assert github.calls("get", "/installation/repositories") == before + 1
    with pytest.raises(Unauthorized):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_RESYNC,
            {},
            blocking=True,
            context=Context(user_id=hass_read_only_user.id),
        )
    await hass.services.async_call(DOMAIN, SERVICE_RESYNC, {}, blocking=True)
    assert github.calls("get", "/installation/repositories") == before + 2


async def test_resync_action_reports_a_failure(
    hass: HomeAssistant, inbox: InboxCoordinator, github: GitHub
) -> None:
    github.reset()
    github.token()
    github.repos(status=502)
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(DOMAIN, SERVICE_RESYNC, {}, blocking=True)
    assert err.value.translation_key == "resync_failed"
    assert err.value.translation_placeholders == {"accounts": "heidrickla"}


async def test_resync_action_with_nothing_loaded(
    hass: HomeAssistant, inbox: InboxCoordinator
) -> None:
    await hass.config_entries.async_unload(inbox.config_entry.entry_id)
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(DOMAIN, SERVICE_RESYNC, {}, blocking=True)
    assert err.value.translation_key == "not_loaded"


# ------------------------------------------------- diagnostics and panel


async def test_diagnostics_redact_the_secrets(
    hass: HomeAssistant, inbox: InboxCoordinator, hass_client: Any, pem: str
) -> None:
    hass.config_entries.async_update_entry(
        inbox.config_entry,
        options={
            **inbox.config_entry.options,
            "notify_targets": ["mobile_app_alex_phone"],
        },
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    diag = await get_diagnostics_for_config_entry(hass, hass_client, inbox.config_entry)
    assert "mobile_app_alex_phone" not in str(diag)
    assert diag["entry"]["options"]["notify_targets"] == "**REDACTED**"
    text = str(diag)
    assert pem not in text
    assert "webhook-secret-for-tests" not in text
    assert "inbox-webhook-id" not in text
    assert diag["entry"]["data"]["private_key"] == "**REDACTED**"
    assert diag["counts"]["issues"] == 2
    assert diag["counts"]["repositories"] == 1
    assert diag["counts"]["ci"]["no_ci"] == 1
    assert diag["sync"]["state"] == "ok"
    assert "ATX buttons" not in text


async def test_the_panel_is_registered_for_administrators_and_served(
    hass: HomeAssistant, inbox: InboxCoordinator, hass_client: Any
) -> None:
    panel = hass.data[frontend.DATA_PANELS][PANEL_URL]
    config = panel.to_response()
    assert config["require_admin"] is True
    assert (
        config["config"]["_panel_custom"]["module_url"]
        == f"{STATIC_URL}/github-inbox-panel.js?v={VERSION}"
    )
    client = await hass_client()
    resp = await client.get(f"{STATIC_URL}/github-inbox-panel.js")
    assert resp.status == 200
    assert "customElements.define" in await resp.text()


async def test_last_delivery_sensor(
    hass: HomeAssistant, inbox: InboxCoordinator, entity_registry: er.EntityRegistry
) -> None:
    entity_id = "sensor.github_heidrickla_last_delivery"
    assert (
        entity_registry.async_get(entity_id).disabled_by
        is er.RegistryEntryDisabler.INTEGRATION
    )
    entity_registry.async_update_entity(entity_id, disabled_by=None)
    await hass.config_entries.async_reload(inbox.config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    coordinator: InboxCoordinator = inbox.config_entry.runtime_data
    assert hass.states.get(entity_id).state == "unknown"
    coordinator.async_receive("ping", "g", {})
    stored = coordinator.store.data["last_delivery"]
    state = hass.states.get(entity_id).state
    assert dt_util.parse_datetime(state) == dt_util.parse_datetime(stored)


# ------------------------------------------------------------------ store


async def test_the_store_survives_a_restart_and_ignores_unknown_keys(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    github: GitHub,
    hass_storage: dict[str, Any],
) -> None:
    hass_storage[f"{DOMAIN}.{config_entry.entry_id}"] = {
        "version": 1,
        "key": f"{DOMAIN}.{config_entry.entry_id}",
        "data": {
            "seen": {KEY: "2099-01-01T00:00:00Z"},
            "deliveries": ["old"],
            "junk": 1,
        },
    }
    github.token()
    github.repos([REPO])
    github.issues([issue(2)])
    inbox = await setup_entry(hass, config_entry)
    assert inbox.store.data["deliveries"] == ["old"]
    assert "junk" not in inbox.store.data
    assert hass.states.get("sensor.github_heidrickla_unseen").state == "0"
    await hass.config_entries.async_unload(config_entry.entry_id)
    saved = hass_storage[f"{DOMAIN}.{config_entry.entry_id}"]["data"]
    assert set(saved["items"]) == {KEY}


async def test_a_corrupt_store_starts_empty(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    github: GitHub,
    hass_storage: dict[str, Any],
) -> None:
    hass_storage[f"{DOMAIN}.{config_entry.entry_id}"] = {
        "version": 1,
        "key": f"{DOMAIN}.{config_entry.entry_id}",
        "data": ["not", "a", "dict"],
    }
    github.token()
    github.repos([REPO])
    github.issues([])
    inbox = await setup_entry(hass, config_entry)
    assert inbox.store.data["deliveries"] == []
