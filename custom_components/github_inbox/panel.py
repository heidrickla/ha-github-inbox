"""The sidebar panel: a web component served from this integration, shown to administrators."""

from __future__ import annotations

from pathlib import Path

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http.server import StaticPathConfig
from homeassistant.core import HomeAssistant, callback

from .const import PANEL_ELEMENT, PANEL_ICON, PANEL_URL, STATIC_URL, VERSION

FRONTEND = Path(__file__).parent / "frontend"
MODULE = "github-inbox-panel.js"


async def async_setup_static(hass: HomeAssistant) -> None:
    await hass.http.async_register_static_paths(
        [StaticPathConfig(STATIC_URL, str(FRONTEND), cache_headers=False)]
    )


async def async_register(hass: HomeAssistant) -> None:
    if frontend.async_panel_exists(hass, PANEL_URL):
        return
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_URL,
        webcomponent_name=PANEL_ELEMENT,
        sidebar_title="GitHub Inbox",
        sidebar_icon=PANEL_ICON,
        # The version busts the browser's copy after an update.
        module_url=f"{STATIC_URL}/{MODULE}?v={VERSION}",
        require_admin=True,
    )


@callback
def async_remove(hass: HomeAssistant) -> None:
    frontend.async_remove_panel(hass, PANEL_URL, warn_if_unknown=False)
