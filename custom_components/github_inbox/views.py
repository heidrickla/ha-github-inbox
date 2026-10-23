"""The pages the config flow's GitHub steps pass through.

The flow opens `/api/github_inbox/start/<nonce>`, which posts the app manifest to GitHub. GitHub returns the browser to
`/api/github_inbox/created` with a code. The flow then opens `/api/github_inbox/install/<nonce>`, which goes on to
GitHub's install page, and GitHub returns the browser to `/api/github_inbox/installed`. The browser has
left Home Assistant for these, so they carry no login. Each answers only for a flow that is waiting: the nonce is
random per flow and doubles as the manifest's state, and an installation id is accepted only by a flow whose own app
can read that installation.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from html import escape
from http import HTTPStatus
from typing import Any

from aiohttp import hdrs, web
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResultType, UnknownFlow
from homeassistant.helpers.http import KEY_HASS, HomeAssistantView
from homeassistant.util.hass_dict import HassKey

from .const import DOMAIN

START_PATH = "/api/github_inbox/start"
INSTALL_PATH = "/api/github_inbox/install"
CREATED_PATH = "/api/github_inbox/created"
INSTALLED_PATH = "/api/github_inbox/installed"
STAGE_CREATE = "create"
STAGE_INSTALL = "install"
_ID_RE = re.compile(r"[0-9]{1,19}")
# Android Chrome and its forks hand github.com pages to the GitHub app, away from the browser holding the manifest
# and the sign-in. An app launch that fails first keeps the rest of the navigation in the browser (crbug.com/330555390).
HOP = "intent://#Intent;scheme=github-inbox-stay;S.browser_fallback_url="


@dataclass(slots=True)
class Pending:
    flow_id: str
    stage: str
    post_url: str = ""
    manifest: dict[str, Any] = field(default_factory=dict)
    install_url: str = ""


def _then(request: web.Request, action: str) -> str:
    """The page script's last statement, after the hop on Android."""
    if request.query.get("go") == "1" or "Android" not in request.headers.get(
        hdrs.USER_AGENT, ""
    ):
        return action
    return f"location.replace({json.dumps(HOP)}+encodeURIComponent(location.origin+location.pathname+'?go=1')+';end')"


DATA_PENDING: HassKey[dict[str, Pending]] = HassKey(f"{DOMAIN}_pending")


def pending(hass: HomeAssistant) -> dict[str, Pending]:
    return hass.data.setdefault(DATA_PENDING, {})


DATA_VIEWS: HassKey[bool] = HassKey(f"{DOMAIN}_views")


@callback
def async_setup(hass: HomeAssistant) -> None:
    """Register the pages once; a first config flow runs before the integration is set up."""
    if hass.data.get(DATA_VIEWS):
        return
    hass.data[DATA_VIEWS] = True
    hass.http.register_view(StartView())
    hass.http.register_view(InstallView())
    hass.http.register_view(CreatedView())
    hass.http.register_view(InstalledView())


PAGE = """<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>GitHub Inbox</title>
<style>:root{{color-scheme:dark}}body{{font:15px/1.5 system-ui,sans-serif;background:#111418;color:#e3e6ea;
display:grid;place-items:center;min-height:100vh;margin:0;padding:1rem}}main{{max-width:30rem;background:#181c21;
border:1px solid #2c3138;border-radius:.75rem;padding:1.25rem 1.5rem}}h1{{font-size:1.15rem;margin:0 0 .5rem}}
p{{color:#9aa3ad;margin:.4rem 0}}button{{font:inherit;background:#3987e5;color:#fff;border:0;border-radius:.4rem;
padding:.45rem 1rem;cursor:pointer;margin-top:.6rem}}</style></head><body><main><h1>{title}</h1>{body}</main>{script}
</body></html>"""


def page(
    title: str, body: str, script: str = "", status: int = HTTPStatus.OK
) -> web.Response:
    return web.Response(
        status=status,
        content_type="text/html",
        text=PAGE.format(title=escape(title), body=body, script=script),
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )


def done_page(message: str) -> web.Response:
    return page(
        "Back to Home Assistant",
        f"<p>{escape(message)}</p><p>This window can be closed.</p>",
        "<script>setTimeout(()=>window.close(),1500)</script>",
    )


def gone_page() -> web.Response:
    return page(
        "Nothing is waiting",
        "<p>No GitHub Inbox setup is waiting for this page. Start it again from "
        "Settings, Devices &amp; services.</p>",
        status=HTTPStatus.NOT_FOUND,
    )


class StartView(HomeAssistantView):
    """Posts the manifest to GitHub; the redirect URLs are this page's own origin."""

    url = START_PATH + "/{nonce}"
    name = "api:github_inbox:start"
    requires_auth = False

    async def get(self, request: web.Request, nonce: str) -> web.Response:
        hass = request.app[KEY_HASS]
        item = pending(hass).get(nonce)
        if item is None or item.stage != STAGE_CREATE:
            return gone_page()
        origin = f"{request.scheme}://{request.host}"
        manifest = {
            **item.manifest,
            "redirect_url": origin + CREATED_PATH,
            "setup_url": origin + INSTALLED_PATH,
        }
        action = f"{item.post_url}?state={nonce}"
        go = _then(request, "document.getElementById('f').submit()")
        body = (
            "<p>Sending the app's description to GitHub. Check the name, then press "
            "Create GitHub App.</p>"
            f'<form id=f method=post action="{escape(action)}">'
            f'<input type=hidden id=m name=manifest value="{escape(json.dumps(manifest))}">'
            "<button type=submit>Continue to GitHub</button></form>"
        )
        # The browser's origin wins over the request's: a proxy in front of Home Assistant can hide the scheme.
        script = (
            "<script>const m=document.getElementById('m'),v=JSON.parse(m.value);"
            f"v.redirect_url=location.origin+{json.dumps(CREATED_PATH)};"
            f"v.setup_url=location.origin+{json.dumps(INSTALLED_PATH)};"
            f"m.value=JSON.stringify(v);{go}</script>"
        )
        return page("Creating the GitHub App", body, script)


class InstallView(HomeAssistantView):
    """Opens GitHub's install page for the app the flow created."""

    url = INSTALL_PATH + "/{nonce}"
    name = "api:github_inbox:install"
    requires_auth = False

    async def get(self, request: web.Request, nonce: str) -> web.Response:
        item = pending(request.app[KEY_HASS]).get(nonce)
        if item is None or item.stage != STAGE_INSTALL or not item.install_url:
            return gone_page()
        target = json.dumps(item.install_url).replace("<", "\\u003c")
        body = (
            "<p>Opening GitHub. Choose the repositories, then press Install.</p>"
            f'<p><a href="{escape(item.install_url)}">Continue to GitHub</a></p>'
        )
        script = f"<script>{_then(request, f'location.replace({target})')}</script>"
        return page("Installing the GitHub App", body, script)


class CreatedView(HomeAssistantView):
    """GitHub's return after the app is created: the code goes to the waiting flow."""

    url = CREATED_PATH
    name = "api:github_inbox:created"
    requires_auth = False

    async def get(self, request: web.Request) -> web.Response:
        hass = request.app[KEY_HASS]
        state = request.query.get("state", "")
        code = request.query.get("code", "")
        item = pending(hass).get(state)
        if item is None or item.stage != STAGE_CREATE or not code:
            return gone_page()
        try:
            await hass.config_entries.flow.async_configure(item.flow_id, {"code": code})
        except UnknownFlow:
            return gone_page()
        return done_page(
            "The app was created. Home Assistant continues with installing it."
        )


class InstalledView(HomeAssistantView):
    """GitHub's return after the app is installed."""

    url = INSTALLED_PATH
    name = "api:github_inbox:installed"
    requires_auth = False

    async def get(self, request: web.Request) -> web.Response:
        hass = request.app[KEY_HASS]
        raw = request.query.get("installation_id", "")
        if not _ID_RE.fullmatch(raw):
            return gone_page()
        waiting = [p for p in pending(hass).values() if p.stage == STAGE_INSTALL]
        for item in waiting:
            try:
                result = await hass.config_entries.flow.async_configure(
                    item.flow_id, {"installation_id": int(raw)}
                )
            except UnknownFlow:
                continue
            if result["type"] is FlowResultType.EXTERNAL_STEP_DONE:
                return done_page(
                    "The app is installed. Home Assistant finishes the setup."
                )
        return gone_page()
