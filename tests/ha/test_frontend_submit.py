"""Each setup form, submitted the way the dialog sends it.

The data comes from the schema the frontend receives, through a port of its
initial-data code (frontend_form.py); Advanced is never opened and nothing is
typed, which is how this setup is meant to be used.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
import voluptuous as vol
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.github_inbox import route
from custom_components.github_inbox.const import CONF_ADVANCED, CONF_APP_NAME, DOMAIN
from tests.ha.conftest import ACCOUNT
from tests.ha.frontend_form import frontend_submission
from tests.ha.test_config_flow import (
    github_ready,  # noqa: F401  (the fixture, used by name below)
    through_github,
)


def _form(result: Any) -> dict[str, Any]:
    """A form with no fields reaches the frontend as an empty schema."""
    return {**result, "data_schema": result["data_schema"] or vol.Schema({})}


async def _open(hass: HomeAssistant) -> Any:
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )


@pytest.mark.usefixtures("github_ready")
async def test_every_step_submits_untouched_to_the_entry(
    hass: HomeAssistant, hass_client_no_auth: Any, mock_route: dict[str, MagicMock]
) -> None:
    result = await _open(hass)
    assert result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], frontend_submission(result)
    )
    assert result["type"] is FlowResultType.EXTERNAL_STEP

    def on_notify(form: Any) -> dict[str, Any]:
        assert form["step_id"] == "notify"
        return frontend_submission(form)

    result = await through_github(hass, await hass_client_no_auth(), result, on_notify)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == ACCOUNT


async def test_the_first_screen_shown_again_after_an_error_submits(
    hass: HomeAssistant, mock_route: dict[str, MagicMock]
) -> None:
    mock_route["hook"].side_effect = route.RouteUnavailableError("cloud")
    result = await _open(hass)
    shown = await hass.config_entries.flow.async_configure(
        result["flow_id"], frontend_submission(result)
    )
    assert shown["type"] is FlowResultType.FORM
    assert shown["errors"]

    mock_route["hook"].side_effect = None
    result = await hass.config_entries.flow.async_configure(
        shown["flow_id"], frontend_submission(shown)
    )
    assert result["type"] is FlowResultType.EXTERNAL_STEP
    hass.config_entries.flow.async_abort(result["flow_id"])


async def test_the_no_route_step_checks_again_on_submit(
    hass: HomeAssistant, mock_route: dict[str, MagicMock]
) -> None:
    mock_route["ready"].return_value = False
    result = await _open(hass)
    assert result["step_id"] == "no_route"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], frontend_submission(_form(result))
    )
    assert result["errors"] == {"base": "still_no_route"}

    mock_route["ready"].return_value = True
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], frontend_submission(_form(result))
    )
    assert result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], frontend_submission(result)
    )
    assert result["type"] is FlowResultType.EXTERNAL_STEP
    hass.config_entries.flow.async_abort(result["flow_id"])


async def test_what_was_typed_in_advanced_comes_back_after_an_error(
    hass: HomeAssistant, mock_route: dict[str, MagicMock]
) -> None:
    mock_route["hook"].side_effect = route.RouteUnavailableError("cloud")
    result = await _open(hass)
    sent = frontend_submission(result)
    sent[CONF_ADVANCED][CONF_APP_NAME] = "Inbox Example"
    shown = await hass.config_entries.flow.async_configure(result["flow_id"], sent)
    assert shown["errors"]

    # The section reopens holding the typed name, and Submit sends it back.
    again = frontend_submission(shown)
    assert again[CONF_ADVANCED][CONF_APP_NAME] == "Inbox Example"
    hass.config_entries.flow.async_abort(shown["flow_id"])
