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
from homeassistant.core import HomeAssistant, is_callback
from homeassistant.exceptions import HomeAssistantError

from custom_components.github_inbox import route
from custom_components.github_inbox.const import ROUTE_CLOUD, ROUTE_EXTERNAL


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
    hass: HomeAssistant, cloud: Any, error: Exception | None
) -> None:
    cloud.async_delete_cloudhook.side_effect = error
    await route.async_delete_cloudhook(hass, "abc")
    cloud.async_delete_cloudhook.assert_awaited_once_with(hass, "abc")


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
