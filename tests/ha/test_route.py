"""The route module against a stand-in for Home Assistant Cloud, which cannot be imported in the test environment."""

from __future__ import annotations

import enum
import sys
import types
from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import homeassistant.components
import pytest
from hass_nabucasa.cloudhooks import CloudhookApiError
from homeassistant.config_entries import ConfigEntryDisabler
from homeassistant.core import HomeAssistant, is_callback
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.github_inbox import route
from custom_components.github_inbox.const import (
    CONF_CLOUDHOOK,
    CONF_WEBHOOK_ID,
    DOMAIN,
    ROUTE_CLOUD,
    ROUTE_EXTERNAL,
)

STORE_KEY = f"{DOMAIN}.cloudhooks"


@pytest.fixture(autouse=True)
def mock_route() -> None:
    """The route module runs for real here."""


class State(enum.Enum):
    CLOUD_CONNECTED = "connected"
    CLOUD_DISCONNECTED = "disconnected"


class CloudNotAvailable(HomeAssistantError):
    pass


@pytest.fixture
def cloud(hass: HomeAssistant) -> Generator[Any]:
    def remote_ui_url(hass: HomeAssistant) -> str:
        raise CloudNotAvailable

    fake = types.SimpleNamespace(
        async_active_subscription=MagicMock(return_value=True),
        async_is_connected=MagicMock(return_value=True),
        async_get_or_create_cloudhook=AsyncMock(
            return_value="https://hooks.nabu.casa/x"
        ),
        async_delete_cloudhook=AsyncMock(),
        async_listen_connection_change=MagicMock(return_value=MagicMock()),
        async_remote_ui_url=remote_ui_url,
        CloudNotAvailable=CloudNotAvailable,
        CloudConnectionState=State,
    )
    hass.config.components.add("cloud")
    with (
        patch.dict(sys.modules, {"homeassistant.components.cloud": fake}),
        patch.object(homeassistant.components, "cloud", fake, create=True),
    ):
        yield fake


async def test_without_the_cloud(hass: HomeAssistant) -> None:
    assert not route.cloud_ready(hass)
    assert not route.cloud_signed_in(hass)
    assert route.available_routes(hass) == []
    assert route.listen_cloud(hass, MagicMock()) is None
    await route.async_delete_cloudhook(hass, "id")
    with pytest.raises(route.RouteUnavailableError):
        await route.async_hook_url(hass, "id", ROUTE_CLOUD)
    with pytest.raises(route.RouteUnavailableError):
        await route.async_hook_url(hass, "id", ROUTE_EXTERNAL)


async def test_an_external_url(hass: HomeAssistant) -> None:
    hass.config.external_url = "https://ha.example"
    assert route.available_routes(hass) == [ROUTE_EXTERNAL]
    assert (
        await route.async_hook_url(hass, "abc", ROUTE_EXTERNAL)
        == "https://ha.example/api/webhook/abc"
    )
    assert route.external_url(hass, "abc") == "https://ha.example/api/webhook/abc"


async def test_the_cloud(hass: HomeAssistant, cloud: Any) -> None:
    assert route.cloud_ready(hass)
    assert route.available_routes(hass) == [ROUTE_CLOUD]
    assert (
        await route.async_hook_url(hass, "abc", ROUTE_CLOUD)
        == "https://hooks.nabu.casa/x"
    )
    cloud.async_get_or_create_cloudhook.assert_awaited_once_with(hass, "abc")
    cloud.async_is_connected.return_value = False
    assert not route.cloud_ready(hass)
    assert route.cloud_signed_in(hass)
    with pytest.raises(route.RouteUnavailableError):
        await route.async_hook_url(hass, "abc", ROUTE_CLOUD)


async def test_a_lapsed_subscription(hass: HomeAssistant, cloud: Any) -> None:
    cloud.async_active_subscription.return_value = False
    assert not route.cloud_ready(hass)
    assert not route.cloud_signed_in(hass)
    assert route.available_routes(hass) == []


@pytest.mark.parametrize(
    "error",
    [
        HomeAssistantError("no"),
        CloudhookApiError("webhook_generate failed"),
        TimeoutError(),
        ValueError("Cloud is not connected"),
    ],
)
async def test_a_cloudhook_the_cloud_refuses(
    hass: HomeAssistant, cloud: Any, error: Exception
) -> None:
    cloud.async_get_or_create_cloudhook.side_effect = error
    with pytest.raises(route.RouteUnavailableError):
        await route.async_hook_url(hass, "abc", ROUTE_CLOUD)


@pytest.mark.parametrize(
    "error",
    [
        None,
        HomeAssistantError("gone"),
        ValueError("unknown"),
        CloudhookApiError("no"),
        TimeoutError(),
    ],
)
async def test_deleting_a_cloudhook(
    hass: HomeAssistant,
    cloud: Any,
    hass_storage: dict[str, Any],
    error: Exception | None,
) -> None:
    seed(hass_storage, "abc", "other")
    cloud.async_delete_cloudhook.side_effect = error
    await route.async_delete_cloudhook(hass, "abc")
    cloud.async_delete_cloudhook.assert_awaited_once_with(hass, "abc")
    # Deleted, or already gone (ValueError), is forgotten; any other failure stays for the next sweep.
    gone = error is None or isinstance(error, ValueError)
    assert made(hass_storage) == (["other"] if gone else ["abc", "other"])


def seed(hass_storage: dict[str, Any], *webhook_ids: str) -> None:
    hass_storage[STORE_KEY] = {
        "version": 1,
        "minor_version": 1,
        "key": STORE_KEY,
        "data": {"made": list(webhook_ids)},
    }


def made(hass_storage: dict[str, Any]) -> list[str]:
    return list(hass_storage[STORE_KEY]["data"]["made"])


def deleted(cloud: Any) -> list[str]:
    return [c.args[1] for c in cloud.async_delete_cloudhook.await_args_list]


async def test_a_cloudhook_is_recorded_before_it_is_made(
    hass: HomeAssistant, cloud: Any, hass_storage: dict[str, Any]
) -> None:
    recorded: list[list[str]] = []

    async def create(_hass: HomeAssistant, webhook_id: str) -> str:
        recorded.append(made(hass_storage))
        return "https://hooks.nabu.casa/x"

    cloud.async_get_or_create_cloudhook.side_effect = create
    await route.async_hook_url(hass, "abc", ROUTE_CLOUD)
    assert recorded == [["abc"]]
    cloud.async_get_or_create_cloudhook.side_effect = ValueError(
        "Cloud is not connected"
    )
    with pytest.raises(route.RouteUnavailableError):
        await route.async_hook_url(hass, "def", ROUTE_CLOUD)
    assert made(hass_storage) == ["abc", "def"]


async def test_the_sweep_deletes_only_what_nothing_holds(
    hass: HomeAssistant, cloud: Any, hass_storage: dict[str, Any]
) -> None:
    seed(
        hass_storage,
        "cloud-entry",
        "disabled-entry",
        "external-entry",
        "held",
        "orphan",
    )
    MockConfigEntry(
        domain=DOMAIN,
        unique_id="1",
        data={CONF_WEBHOOK_ID: "cloud-entry", CONF_CLOUDHOOK: True},
    ).add_to_hass(hass)
    MockConfigEntry(
        domain=DOMAIN,
        unique_id="2",
        disabled_by=ConfigEntryDisabler.USER,
        data={CONF_WEBHOOK_ID: "disabled-entry", CONF_CLOUDHOOK: True},
    ).add_to_hass(hass)
    MockConfigEntry(
        domain=DOMAIN,
        unique_id="3",
        data={CONF_WEBHOOK_ID: "external-entry", CONF_CLOUDHOOK: False},
    ).add_to_hass(hass)
    route.hold(hass, "held")
    route.hold(hass, "held")
    await route.async_sweep(hass)
    # Only recorded cloudhooks are candidates: one this integration never made is never deleted.
    assert deleted(cloud) == ["external-entry", "orphan"]
    assert made(hass_storage) == ["cloud-entry", "disabled-entry", "held"]

    route.release(hass, "held")
    await route.async_sweep(hass)
    assert deleted(cloud) == ["external-entry", "orphan"]
    route.release(hass, "held")
    await route.async_sweep(hass)
    assert deleted(cloud) == ["external-entry", "orphan", "held"]
    assert made(hass_storage) == ["cloud-entry", "disabled-entry"]


async def test_a_sweep_that_cannot_delete_keeps_the_record(
    hass: HomeAssistant, cloud: Any, hass_storage: dict[str, Any]
) -> None:
    seed(hass_storage, "orphan")
    cloud.async_is_connected.return_value = False
    await route.async_sweep(hass)
    cloud.async_delete_cloudhook.assert_not_awaited()
    cloud.async_is_connected.return_value = True
    cloud.async_delete_cloudhook.side_effect = TimeoutError()
    await route.async_sweep(hass)
    assert made(hass_storage) == ["orphan"]
    cloud.async_delete_cloudhook.side_effect = None
    await route.async_sweep(hass)
    assert deleted(cloud) == ["orphan", "orphan"]
    assert made(hass_storage) == []


async def test_the_startup_sweep_waits_for_the_cloud(
    hass: HomeAssistant, cloud: Any, hass_storage: dict[str, Any]
) -> None:
    seed(hass_storage, "orphan")
    cloud.async_is_connected.return_value = False
    route.async_sweep_when_connected(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    cloud.async_delete_cloudhook.assert_not_awaited()
    changed = cloud.async_listen_connection_change.call_args.args[1]
    unsub = cloud.async_listen_connection_change.return_value
    changed(State.CLOUD_DISCONNECTED)
    unsub.assert_not_called()
    cloud.async_is_connected.return_value = True
    changed(State.CLOUD_CONNECTED)
    changed(State.CLOUD_CONNECTED)
    await hass.async_block_till_done(wait_background_tasks=True)
    unsub.assert_called_once()
    assert deleted(cloud) == ["orphan"]


async def test_the_startup_sweep_runs_at_once_when_connected(
    hass: HomeAssistant, cloud: Any, hass_storage: dict[str, Any]
) -> None:
    seed(hass_storage, "orphan")
    route.async_sweep_when_connected(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert deleted(cloud) == ["orphan"]
    cloud.async_listen_connection_change.assert_not_called()


async def test_connection_changes_are_reported_on_the_loop(
    hass: HomeAssistant, cloud: Any
) -> None:
    calls: list[bool] = []
    unsub = route.listen_cloud(hass, calls.append)
    assert unsub is cloud.async_listen_connection_change.return_value
    changed = cloud.async_listen_connection_change.call_args.args[1]
    # The dispatcher runs a target that is not a callback in an executor thread.
    assert is_callback(changed)
    changed(State.CLOUD_DISCONNECTED)
    changed(State.CLOUD_CONNECTED)
    assert calls == [False, True]
