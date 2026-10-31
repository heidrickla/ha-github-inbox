"""The config flow: app creation through GitHub's pages, installation, reauth, reconfigure and options."""

from __future__ import annotations

import html
import json
import re
from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.network import NoURLAvailableError
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.github_inbox import route, views
from custom_components.github_inbox.const import (
    APP_EVENTS,
    CONF_ADVANCED,
    CONF_APP_ID,
    CONF_APP_NAME,
    CONF_CLOUDHOOK,
    CONF_INCLUDE_BOTS,
    CONF_INCLUDE_OWN,
    CONF_ME,
    CONF_NOTIFY_CI,
    CONF_NOTIFY_ON,
    CONF_NOTIFY_TARGETS,
    CONF_ORGANIZATION,
    CONF_PERSISTENT,
    CONF_PRIVATE_KEY,
    CONF_ROUTE,
    CONF_WEBHOOK_ID,
    CONF_WEBHOOK_SECRET,
    DOMAIN,
    NOTIFY_OPENED,
    ROUTE_CLOUD,
    ROUTE_EXTERNAL,
)
from tests.ha.conftest import (
    ACCOUNT,
    ACCOUNT_ID,
    API,
    APP_ID,
    APP_URL,
    HOOK_URL,
    INSTALLATION_ID,
    WEBHOOK_ID,
    GitHub,
    entry_data,
    setup_entry,
)
from tests.ha.frontend_form import frontend_submission
from tests.payloads import REPO

NEW_APP_ID = 5151
CODE = "manifest-code"


def nonce_of(result: Any) -> str:
    return str(result["url"]).rsplit("/", 1)[1]


def creds(pem: str, app_id: int = APP_ID) -> dict[str, Any]:
    return {
        "id": app_id,
        "slug": "ha-inbox-test",
        "html_url": APP_URL,
        "pem": pem,
        "webhook_secret": "new-secret",
        "owner": {"login": ACCOUNT, "id": ACCOUNT_ID},
    }


def installation(account_id: int = ACCOUNT_ID, **extra: Any) -> dict[str, Any]:
    return {
        "id": INSTALLATION_ID,
        "account": {"login": ACCOUNT, "id": account_id},
        **extra,
    }


@pytest.fixture
def github_ready(
    github: GitHub, aioclient_mock: AiohttpClientMocker, pem: str
) -> GitHub:
    github.token()
    github.repos([REPO])
    github.issues([])
    aioclient_mock.post(
        f"{API}/app-manifests/{CODE}/conversions", status=201, json=creds(pem)
    )
    aioclient_mock.get(
        f"{API}/app/installations/{INSTALLATION_ID}", json=installation()
    )
    return github


async def start(hass: HomeAssistant, **user: Any) -> Any:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return await hass.config_entries.flow.async_configure(
        result["flow_id"],
        advanced(**user),
    )


def advanced(**user: Any) -> dict[str, Any]:
    """The first form's input: everything sits in the collapsed Advanced section."""
    return {
        CONF_ADVANCED: {CONF_APP_NAME: "HA Inbox test", CONF_ROUTE: ROUTE_CLOUD, **user}
    }


async def through_github(
    hass: HomeAssistant,
    client: Any,
    result: Any,
    on_notify: Callable[[Any], dict[str, Any]] = lambda _: {},
) -> Any:
    """The browser's trip through GitHub: the manifest page, creation, installation, then the notify step."""
    assert result["type"] is FlowResultType.EXTERNAL_STEP
    assert result["step_id"] == "create"
    nonce = nonce_of(result)
    page = await client.get(f"{views.START_PATH}/{nonce}")
    assert page.status == 200
    assert (
        await client.get(f"{views.CREATED_PATH}?state={nonce}&code={CODE}")
    ).status == 200
    result = await hass.config_entries.flow.async_configure(result["flow_id"])
    assert result["type"] is FlowResultType.EXTERNAL_STEP
    assert result["step_id"] == "install"
    assert f"{views.INSTALL_PATH}/" in result["url"]
    page = await (await client.get(f"{views.INSTALL_PATH}/{nonce_of(result)}")).text()
    assert f'location.replace("{APP_URL}/installations/new")' in page
    resp = await client.get(f"{views.INSTALLED_PATH}?installation_id={INSTALLATION_ID}")
    assert resp.status == 200
    assert "finishes the setup" in await resp.text()
    result = await hass.config_entries.flow.async_configure(result["flow_id"])
    if result["type"] is FlowResultType.FORM and result["step_id"] == "notify":
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], on_notify(result)
        )
    return result


async def test_the_whole_setup(
    hass: HomeAssistant,
    hass_client_no_auth: Any,
    github_ready: GitHub,
    mock_route: dict[str, MagicMock],
    pem: str,
) -> None:
    result = await start(hass)
    client = await hass_client_no_auth()
    nonce = nonce_of(result)
    page = html.unescape(await (await client.get(f"{views.START_PATH}/{nonce}")).text())
    assert f'action="https://github.com/settings/apps/new?state={nonce}"' in page
    manifest = json.loads(re.search(r'name=manifest value="(.*?)">', page).group(1))
    assert manifest["hook_attributes"] == {"url": HOOK_URL, "active": True}
    assert manifest["public"] is False
    assert manifest["default_events"] == list(APP_EVENTS)
    assert manifest["default_permissions"] == {
        "actions": "read",
        "issues": "read",
        "metadata": "read",
        "pull_requests": "read",
    }
    assert manifest["redirect_url"].endswith(views.CREATED_PATH)
    assert "location.origin" in page

    result = await through_github(hass, client, result)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == ACCOUNT
    data = result["data"]
    assert data[CONF_APP_ID] == APP_ID
    assert data[CONF_PRIVATE_KEY] == pem
    assert data[CONF_WEBHOOK_SECRET] == "new-secret"
    assert data["app_slug"] == "ha-inbox-test"
    assert data["app_url"] == APP_URL
    assert data[CONF_CLOUDHOOK] is True
    assert data["installation_id"] == INSTALLATION_ID
    assert data["account_id"] == ACCOUNT_ID
    assert result["options"][CONF_PERSISTENT] is True
    assert result["options"][CONF_ME] == [ACCOUNT]
    assert result["result"].unique_id == str(ACCOUNT_ID)
    assert mock_route["hook"].await_args.args[1:] == (
        data[CONF_WEBHOOK_ID],
        ROUTE_CLOUD,
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    mock_route["delete"].assert_not_called()
    assert views.pending(hass) == {}
    assert held(hass) == {}


async def test_the_last_step_offers_the_phones_already_ticked(
    hass: HomeAssistant,
    hass_client_no_auth: Any,
    github_ready: GitHub,
    mock_route: dict[str, MagicMock],
) -> None:
    async_mock_service(hass, "notify", "mobile_app_pixel_9")
    async_mock_service(hass, "notify", "family_email")
    async_mock_service(hass, "notify", "send_message")
    seen: dict[str, Any] = {}

    def on_notify(form: Any) -> dict[str, Any]:
        seen.update(form)
        return {CONF_NOTIFY_TARGETS: ["mobile_app_pixel_9"], CONF_PERSISTENT: False}

    result = await start(hass)
    result = await through_github(hass, await hass_client_no_auth(), result, on_notify)
    schema = {str(k): k for k in seen["data_schema"].schema}
    assert schema[CONF_NOTIFY_TARGETS].default() == ["mobile_app_pixel_9"]
    assert seen["data_schema"].schema[schema[CONF_NOTIFY_TARGETS]].config[
        "options"
    ] == ["family_email", "mobile_app_pixel_9"]
    assert schema[CONF_PERSISTENT].default() is True
    assert seen["description_placeholders"] == {"account": ACCOUNT, "repositories": "1"}
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"][CONF_NOTIFY_TARGETS] == ["mobile_app_pixel_9"]
    assert result["options"][CONF_PERSISTENT] is False


async def test_closing_the_last_step_drops_the_cloudhook(
    hass: HomeAssistant,
    hass_client_no_auth: Any,
    github_ready: GitHub,
    mock_route: dict[str, MagicMock],
) -> None:
    flow_ids: list[str] = []

    def on_notify(form: Any) -> dict[str, Any]:
        flow_ids.append(form["flow_id"])
        raise _Closed

    result = await start(hass)
    with pytest.raises(_Closed):
        await through_github(hass, await hass_client_no_auth(), result, on_notify)
    hass.config_entries.flow.async_abort(flow_ids[0])
    await hass.async_block_till_done(wait_background_tasks=True)
    mock_route["delete"].assert_awaited_once()
    assert not hass.config_entries.async_entries(DOMAIN)


class _Closed(Exception):
    """The user closes the dialog at the notify step."""


def held(hass: HomeAssistant) -> dict[str, int]:
    return dict(hass.data.get(route.DATA_HELD) or {})


async def test_opening_the_form_sweeps_cloudhooks_left_by_a_restart(
    hass: HomeAssistant,
) -> None:
    with patch(f"{route.__name__}.async_sweep", AsyncMock()) as sweep:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        await hass.async_block_till_done(wait_background_tasks=True)
    sweep.assert_awaited_once_with(hass)
    hass.config_entries.flow.async_abort(result["flow_id"])


async def test_the_whole_setup_on_the_external_route(
    hass: HomeAssistant,
    hass_client_no_auth: Any,
    github_ready: GitHub,
    mock_route: dict[str, MagicMock],
) -> None:
    mock_route["ready"].return_value = False
    mock_route["external"].return_value = "https://ha.example/api/webhook/x"
    mock_route["hook"].return_value = "https://ha.example/api/webhook/x"
    result = await start(hass, **{CONF_ROUTE: ROUTE_EXTERNAL})
    client = await hass_client_no_auth()
    result = await through_github(hass, client, result)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_CLOUDHOOK] is False
    await hass.async_block_till_done(wait_background_tasks=True)
    mock_route["delete"].assert_not_called()
    mock_route["listen"].assert_not_called()


async def test_github_returns_to_the_origin_the_browser_used(
    hass: HomeAssistant, hass_client: Any
) -> None:
    # Views register before the client freezes the router.
    for domain in ("config", "webhook", DOMAIN):
        assert await async_setup_component(hass, domain, {})
    client = await hass_client()
    resp = await client.post(
        "/api/config/config_entries/flow", json={"handler": DOMAIN}
    )
    flow = await resp.json()
    resp = await client.post(
        f"/api/config/config_entries/flow/{flow['flow_id']}",
        json=advanced(),
    )
    step = await resp.json()
    base = client.make_url("/")
    origin = f"{base.scheme}://{base.host}:{base.port}"
    assert step["url"].startswith(f"{origin}{views.START_PATH}/")
    hass.config_entries.flow.async_abort(flow["flow_id"])


APP_AGENT = "Mozilla/5.0 (Linux; Android 16) Home Assistant/2026.10.1-full (Android 16; Pixel 9)"


def urls(cloud: str | None, internal: str | None) -> Callable[..., str]:
    def get_url(hass: HomeAssistant, **where: Any) -> str:
        url = internal if where.get("allow_external") is False else cloud
        if url is None:
            raise NoURLAvailableError
        return url

    return get_url


@pytest.mark.parametrize(
    ("host", "cloud", "internal", "expected"),
    [
        (
            None,
            "https://abc.ui.nabu.casa",
            "https://homeassistant.example:8123",
            "https://abc.ui.nabu.casa",
        ),
        (
            "abc.ui.nabu.casa",
            "http://abc.ui.nabu.casa",
            "https://homeassistant.example:8123",
            "https://homeassistant.example:8123",
        ),
        (
            None,
            None,
            "https://homeassistant.example:8123",
            "https://homeassistant.example:8123",
        ),
        (None, None, None, None),
    ],
)
async def test_the_companion_app_is_sent_to_another_address(
    hass: HomeAssistant,
    hass_client: Any,
    host: str | None,
    cloud: str | None,
    internal: str | None,
    expected: str | None,
) -> None:
    for domain in ("config", "webhook", DOMAIN):
        assert await async_setup_component(hass, domain, {})
    client = await hass_client()
    headers = {"User-Agent": APP_AGENT}
    if host:
        headers["Host"] = host
    resp = await client.post(
        "/api/config/config_entries/flow", json={"handler": DOMAIN}, headers=headers
    )
    flow = await resp.json()
    with patch(
        "custom_components.github_inbox.config_flow.get_url", urls(cloud, internal)
    ):
        resp = await client.post(
            f"/api/config/config_entries/flow/{flow['flow_id']}",
            json=advanced(),
            headers=headers,
        )
    step = await resp.json()
    if expected is None:
        base = client.make_url("/")
        expected = f"{base.scheme}://{base.host}:{base.port}"
    assert step["url"].startswith(f"{expected}{views.START_PATH}/")
    hass.config_entries.flow.async_abort(flow["flow_id"])


async def test_without_a_request_or_a_url_the_step_url_is_a_path(
    hass: HomeAssistant,
) -> None:
    with patch(
        "custom_components.github_inbox.config_flow.get_url",
        side_effect=NoURLAvailableError,
    ):
        result = await start(hass)
    assert result["url"].startswith(views.START_PATH + "/")
    hass.config_entries.flow.async_abort(result["flow_id"])


@pytest.mark.parametrize(
    ("agent", "query", "hops"),
    [
        ("Mozilla/5.0 (Linux; Android 16; Pixel 11) Chrome/150.0 Mobile", "", True),
        (
            "Mozilla/5.0 (Linux; Android 16; Pixel 11) Chrome/150.0 Mobile",
            "?go=1",
            False,
        ),
        ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/150.0", "", False),
        (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 26_0 like Mac OS X) Safari/604.1",
            "",
            False,
        ),
    ],
)
async def test_an_android_browser_hops_through_a_failed_app_launch_first(
    hass: HomeAssistant, hass_client_no_auth: Any, agent: str, query: str, hops: bool
) -> None:
    result = await start(hass)
    client = await hass_client_no_auth()
    url = f"{views.START_PATH}/{nonce_of(result)}{query}"
    page = await (await client.get(url, headers={"User-Agent": agent})).text()
    assert ("intent://#Intent;scheme=github-inbox-stay;" in page) is hops
    assert ("'?go=1'" in page) is hops
    assert ("getElementById('f').submit()" in page) is not hops
    hass.config_entries.flow.async_abort(result["flow_id"])


@pytest.mark.parametrize(
    ("agent", "hops"),
    [
        ("Mozilla/5.0 (Linux; Android 16; Pixel 11) Chrome/150.0 Mobile", True),
        ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/150.0", False),
    ],
)
async def test_the_install_page_hops_on_android_then_opens_github(
    hass: HomeAssistant,
    hass_client_no_auth: Any,
    github_ready: GitHub,
    mock_route: dict[str, MagicMock],
    agent: str,
    hops: bool,
) -> None:
    result = await start(hass)
    client = await hass_client_no_auth()
    await client.get(f"{views.CREATED_PATH}?state={nonce_of(result)}&code={CODE}")
    result = await hass.config_entries.flow.async_configure(result["flow_id"])
    nonce = nonce_of(result)
    url = f"{views.INSTALL_PATH}/{nonce}"
    page = await (await client.get(url, headers={"User-Agent": agent})).text()
    assert ("intent://#Intent;scheme=github-inbox-stay;" in page) is hops
    opens = f'location.replace("{APP_URL}/installations/new")'
    assert (opens in page) is not hops
    page = await (await client.get(f"{url}?go=1", headers={"User-Agent": agent})).text()
    assert opens in page
    assert (await client.get(f"{views.START_PATH}/{nonce}")).status == 404
    hass.config_entries.flow.async_abort(result["flow_id"])
    assert (await client.get(url)).status == 404


async def test_an_organization_app(
    hass: HomeAssistant, hass_client_no_auth: Any
) -> None:
    result = await start(hass, **{CONF_ORGANIZATION: "my-org"})
    client = await hass_client_no_auth()
    page = await (await client.get(f"{views.START_PATH}/{nonce_of(result)}")).text()
    assert "https://github.com/organizations/my-org/settings/apps/new?state=" in page


@pytest.mark.parametrize(
    ("user", "field", "error"),
    [
        ({CONF_APP_NAME: "   "}, CONF_APP_NAME, "invalid_name"),
        ({CONF_APP_NAME: "x" * 35}, CONF_APP_NAME, "invalid_name"),
        ({CONF_ORGANIZATION: "not an org"}, CONF_ORGANIZATION, "invalid_organization"),
    ],
)
async def test_user_step_errors(
    hass: HomeAssistant, user: dict[str, Any], field: str, error: str
) -> None:
    result = await start(hass, **user)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {field: error}


async def test_a_route_that_went_away_while_the_form_was_open(
    hass: HomeAssistant, mock_route: dict[str, MagicMock]
) -> None:
    mock_route["external"].return_value = "https://ha.example/api/webhook/x"
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    mock_route["external"].return_value = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], advanced(**{CONF_ROUTE: ROUTE_EXTERNAL})
    )
    assert result["errors"] == {CONF_ROUTE: "route_unavailable"}


async def test_a_cloudhook_that_cannot_be_made(
    hass: HomeAssistant, mock_route: dict[str, MagicMock]
) -> None:
    mock_route["hook"].side_effect = route.RouteUnavailableError("cloud")
    result = await start(hass)
    assert result["errors"] == {CONF_ROUTE: "route_unavailable"}


async def test_without_a_route_the_flow_says_how_and_checks_again(
    hass: HomeAssistant, mock_route: dict[str, MagicMock]
) -> None:
    mock_route["ready"].return_value = False
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "no_route"
    assert result["errors"] == {}
    assert result["description_placeholders"] == {
        "cloud_url": "/config/cloud",
        "network_url": "/config/network",
    }
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "no_route"
    assert result["errors"] == {"base": "still_no_route"}
    mock_route["ready"].return_value = True
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    hass.config_entries.flow.async_abort(result["flow_id"])


async def test_submitting_the_form_untouched_starts_on_github(
    hass: HomeAssistant, hass_client_no_auth: Any, mock_route: dict[str, MagicMock]
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert list(result["data_schema"].schema) == [CONF_ADVANCED]
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], frontend_submission(result)
    )
    assert result["type"] is FlowResultType.EXTERNAL_STEP
    client = await hass_client_no_auth()
    page = html.unescape(
        await (await client.get(f"{views.START_PATH}/{nonce_of(result)}")).text()
    )
    manifest = json.loads(re.search(r'name=manifest value="(.*?)">', page).group(1))
    assert re.fullmatch(r"HA Inbox [0-9a-f]{6}", manifest["name"])
    assert 'action="https://github.com/settings/apps/new?state=' in page
    assert mock_route["hook"].await_args.args[2] == ROUTE_CLOUD
    hass.config_entries.flow.async_abort(result["flow_id"])


async def test_an_external_route(
    hass: HomeAssistant, mock_route: dict[str, MagicMock]
) -> None:
    mock_route["ready"].return_value = False
    mock_route["external"].return_value = "https://ha.example/api/webhook/x"
    result = await start(hass, **{CONF_ROUTE: ROUTE_EXTERNAL})
    assert result["type"] is FlowResultType.EXTERNAL_STEP
    hass.config_entries.flow.async_abort(result["flow_id"])
    await hass.async_block_till_done()
    mock_route["delete"].assert_not_called()


async def test_a_closed_flow_drops_its_cloudhook_and_pages(
    hass: HomeAssistant, hass_client_no_auth: Any, mock_route: dict[str, MagicMock]
) -> None:
    result = await start(hass)
    nonce = nonce_of(result)
    webhook_id = mock_route["hook"].await_args.args[1]
    assert held(hass) == {webhook_id: 1}
    hass.config_entries.flow.async_abort(result["flow_id"])
    await hass.async_block_till_done()
    assert held(hass) == {}
    mock_route["delete"].assert_awaited_once_with(hass, webhook_id)
    client = await hass_client_no_auth()
    assert (await client.get(f"{views.START_PATH}/{nonce}")).status == 404
    assert (
        await client.get(f"{views.CREATED_PATH}?state={nonce}&code=x")
    ).status == 404


async def test_pages_answer_only_a_waiting_flow(
    hass: HomeAssistant, hass_client_no_auth: Any
) -> None:
    result = await start(hass)
    nonce = nonce_of(result)
    client = await hass_client_no_auth()
    assert (await client.get(f"{views.CREATED_PATH}?state={nonce}")).status == 404
    assert (await client.get(f"{views.CREATED_PATH}?state=guess&code=x")).status == 404
    assert (
        await client.get(f"{views.INSTALLED_PATH}?installation_id=abc")
    ).status == 404
    assert (await client.get(f"{views.INSTALLED_PATH}?installation_id=1")).status == 404
    views.pending(hass)["stale"] = views.Pending("no-such-flow", views.STAGE_CREATE)
    views.pending(hass)["waiting"] = views.Pending(
        result["flow_id"], views.STAGE_INSTALL
    )
    for raw in ("%C2%B2", "-1", "1" * 40, ""):
        resp = await client.get(f"{views.INSTALLED_PATH}?installation_id={raw}")
        assert resp.status == 404
    del views.pending(hass)["waiting"]
    assert (await client.get(f"{views.CREATED_PATH}?state=stale&code=x")).status == 404
    views.pending(hass)["stale2"] = views.Pending("no-such-flow", views.STAGE_INSTALL)
    assert (await client.get(f"{views.INSTALLED_PATH}?installation_id=1")).status == 404


async def test_a_failed_conversion_aborts(
    hass: HomeAssistant, hass_client_no_auth: Any, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.post(f"{API}/app-manifests/{CODE}/conversions", status=422, json={})
    result = await start(hass)
    client = await hass_client_no_auth()
    await client.get(f"{views.CREATED_PATH}?state={nonce_of(result)}&code={CODE}")
    result = await hass.config_entries.flow.async_configure(result["flow_id"])
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "conversion_failed"


@pytest.mark.parametrize(
    ("status", "body"),
    [(404, {}), (200, installation(suspended_at="2026-10-01T00:00:00Z"))],
)
async def test_an_installation_the_app_cannot_use_is_refused(
    hass: HomeAssistant,
    hass_client_no_auth: Any,
    aioclient_mock: AiohttpClientMocker,
    pem: str,
    status: int,
    body: dict[str, Any],
) -> None:
    aioclient_mock.post(
        f"{API}/app-manifests/{CODE}/conversions", status=201, json=creds(pem)
    )
    aioclient_mock.get(
        f"{API}/app/installations/{INSTALLATION_ID}", status=status, json=body
    )
    result = await start(hass)
    client = await hass_client_no_auth()
    await client.get(f"{views.CREATED_PATH}?state={nonce_of(result)}&code={CODE}")
    result = await hass.config_entries.flow.async_configure(result["flow_id"])
    resp = await client.get(f"{views.INSTALLED_PATH}?installation_id={INSTALLATION_ID}")
    assert resp.status == 404
    progress = hass.config_entries.flow.async_get(result["flow_id"])
    assert progress["step_id"] == "install"


async def test_an_installation_github_cannot_list_aborts(
    hass: HomeAssistant,
    hass_client_no_auth: Any,
    github: GitHub,
    aioclient_mock: AiohttpClientMocker,
    pem: str,
) -> None:
    github.token()
    github.repos(status=500)
    aioclient_mock.post(
        f"{API}/app-manifests/{CODE}/conversions", status=201, json=creds(pem)
    )
    aioclient_mock.get(
        f"{API}/app/installations/{INSTALLATION_ID}", json=installation()
    )
    result = await start(hass)
    client = await hass_client_no_auth()
    result = await through_github(hass, client, result)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"


async def test_an_account_set_up_twice_aborts(
    hass: HomeAssistant,
    hass_client_no_auth: Any,
    github_ready: GitHub,
    config_entry: MockConfigEntry,
) -> None:
    config_entry.add_to_hass(hass)
    result = await start(hass)
    client = await hass_client_no_auth()
    result = await through_github(hass, client, result)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


# ---------------------------------------------------------------- reauth


@pytest.fixture
async def loaded(
    hass: HomeAssistant, config_entry: MockConfigEntry, github: GitHub
) -> MockConfigEntry:
    github.token()
    github.repos([REPO])
    github.issues([])
    await setup_entry(hass, config_entry)
    return config_entry


async def reauth(hass: HomeAssistant, entry: MockConfigEntry) -> Any:
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    assert result["description_placeholders"]["account"] == ACCOUNT
    return await hass.config_entries.flow.async_configure(result["flow_id"], {})


async def test_reauth_with_the_app_still_installed(
    hass: HomeAssistant, loaded: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(f"{API}/app", json={"id": APP_ID})
    aioclient_mock.get(
        f"{API}/app/installations/{INSTALLATION_ID}", json=installation()
    )
    result = await reauth(hass, loaded)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"


async def test_reauth_for_another_account_aborts(
    hass: HomeAssistant, loaded: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(f"{API}/app", json={"id": APP_ID})
    aioclient_mock.get(
        f"{API}/app/installations/{INSTALLATION_ID}", json=installation(account_id=99)
    )
    result = await reauth(hass, loaded)
    assert result["reason"] == "wrong_account"


@pytest.mark.parametrize(
    ("installation_status", "body", "error"),
    [
        (502, {}, "cannot_connect"),
        (200, installation(suspended_at="2026-10-01T00:00:00Z"), "suspended"),
    ],
)
async def test_reauth_errors(
    hass: HomeAssistant,
    loaded: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    installation_status: int,
    body: dict[str, Any],
    error: str,
) -> None:
    aioclient_mock.get(f"{API}/app", json={"id": APP_ID})
    aioclient_mock.get(
        f"{API}/app/installations/{INSTALLATION_ID}",
        status=installation_status,
        json=body,
    )
    result = await reauth(hass, loaded)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}


async def test_reauth_after_the_app_was_uninstalled_reinstalls_it(
    hass: HomeAssistant,
    loaded: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    hass_client_no_auth: Any,
) -> None:
    aioclient_mock.get(f"{API}/app", json={"id": APP_ID})
    answers = iter([(404, {}), (200, installation())])

    async def lookup(method: str, url: Any, data: Any) -> Any:
        from pytest_homeassistant_custom_component.test_util.aiohttp import (
            AiohttpClientMockResponse,
        )

        status, payload = next(answers)
        return AiohttpClientMockResponse(method, url, status=status, json=payload)

    aioclient_mock.get(f"{API}/app/installations/{INSTALLATION_ID}", side_effect=lookup)
    result = await reauth(hass, loaded)
    assert result["type"] is FlowResultType.EXTERNAL_STEP
    assert result["step_id"] == "install"
    client = await hass_client_no_auth()
    resp = await client.get(f"{views.INSTALLED_PATH}?installation_id={INSTALLATION_ID}")
    assert resp.status == 200
    result = await hass.config_entries.flow.async_configure(result["flow_id"])
    assert result["reason"] == "reauth_successful"


async def test_reauth_with_a_refused_key_makes_a_new_app_on_the_same_webhook(
    hass: HomeAssistant,
    loaded: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    hass_client_no_auth: Any,
    mock_route: dict[str, MagicMock],
    pem: str,
) -> None:
    aioclient_mock.get(f"{API}/app", status=401, json={})
    aioclient_mock.post(
        f"{API}/app-manifests/{CODE}/conversions",
        status=201,
        json=creds(pem, NEW_APP_ID),
    )
    aioclient_mock.get(
        f"{API}/app/installations/{INSTALLATION_ID}", json=installation()
    )
    result = await reauth(hass, loaded)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], advanced(**{CONF_APP_NAME: "HA Inbox again"})
    )
    assert mock_route["hook"].await_args.args[1:] == (WEBHOOK_ID, ROUTE_CLOUD)
    client = await hass_client_no_auth()
    result = await through_github(hass, client, result)
    assert result["reason"] == "reauth_successful"
    await hass.async_block_till_done(wait_background_tasks=True)
    assert loaded.data[CONF_APP_ID] == NEW_APP_ID
    assert loaded.data[CONF_WEBHOOK_ID] == WEBHOOK_ID
    assert loaded.data[CONF_WEBHOOK_SECRET] == "new-secret"
    mock_route["delete"].assert_not_called()


async def test_reauth_onto_the_external_route_drops_the_cloudhook(
    hass: HomeAssistant,
    loaded: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    hass_client_no_auth: Any,
    mock_route: dict[str, MagicMock],
    pem: str,
) -> None:
    aioclient_mock.get(f"{API}/app", status=401, json={})
    aioclient_mock.post(
        f"{API}/app-manifests/{CODE}/conversions",
        status=201,
        json=creds(pem, NEW_APP_ID),
    )
    aioclient_mock.get(
        f"{API}/app/installations/{INSTALLATION_ID}", json=installation()
    )
    mock_route["external"].return_value = "https://ha.example/api/webhook/x"
    mock_route["hook"].return_value = "https://ha.example/api/webhook/x"
    result = await reauth(hass, loaded)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        advanced(**{CONF_APP_NAME: "HA Inbox again", CONF_ROUTE: ROUTE_EXTERNAL}),
    )
    client = await hass_client_no_auth()
    result = await through_github(hass, client, result)
    assert result["reason"] == "reauth_successful"
    await hass.async_block_till_done(wait_background_tasks=True)
    assert loaded.data[CONF_CLOUDHOOK] is False
    mock_route["delete"].assert_awaited_once_with(hass, WEBHOOK_ID)


# ----------------------------------------------------------- reconfigure


async def test_reconfigure_moves_the_webhook_off_the_cloud(
    hass: HomeAssistant,
    loaded: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    mock_route: dict[str, MagicMock],
) -> None:
    mock_route["external"].return_value = "https://ha.example/api/webhook/x"
    mock_route["hook"].return_value = "https://ha.example/api/webhook/x"
    aioclient_mock.patch(f"{API}/app/hook/config", status=204)
    result = await loaded.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ROUTE: ROUTE_EXTERNAL}
    )
    assert result["reason"] == "reconfigure_successful"
    assert loaded.data[CONF_CLOUDHOOK] is False
    mock_route["delete"].assert_awaited_once_with(hass, WEBHOOK_ID)
    patches = [c[2] for c in aioclient_mock.mock_calls if c[0].lower() == "patch"]
    assert patches == [
        {"url": "https://ha.example/api/webhook/x", "content_type": "json"}
    ]


@pytest.mark.parametrize(
    ("status", "errors"),
    [
        (401, {"base": "invalid_auth"}),
        (500, {"base": "cannot_connect"}),
        (None, {CONF_ROUTE: "route_unavailable"}),
    ],
)
async def test_reconfigure_errors(
    hass: HomeAssistant,
    loaded: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    mock_route: dict[str, MagicMock],
    status: int | None,
    errors: dict[str, str],
) -> None:
    if status is None:
        mock_route["hook"].side_effect = route.RouteUnavailableError("cloud")
    else:
        aioclient_mock.patch(f"{API}/app/hook/config", status=status, json={})
    result = await loaded.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ROUTE: ROUTE_CLOUD}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == errors
    assert loaded.data[CONF_CLOUDHOOK] is True
    mock_route["delete"].assert_not_called()


@pytest.fixture
async def loaded_external(
    hass: HomeAssistant, pem: str, github: GitHub
) -> MockConfigEntry:
    github.token()
    github.repos([REPO])
    github.issues([])
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=ACCOUNT,
        unique_id=str(ACCOUNT_ID),
        data=entry_data(pem, **{CONF_CLOUDHOOK: False}),
        options={},
    )
    await setup_entry(hass, entry)
    return entry


async def test_reconfigure_moves_the_webhook_onto_the_cloud(
    hass: HomeAssistant,
    loaded_external: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    mock_route: dict[str, MagicMock],
) -> None:
    aioclient_mock.patch(f"{API}/app/hook/config", status=204)
    result = await loaded_external.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ROUTE: ROUTE_CLOUD}
    )
    assert result["reason"] == "reconfigure_successful"
    assert loaded_external.data[CONF_CLOUDHOOK] is True
    mock_route["delete"].assert_not_called()
    assert held(hass) == {}


async def test_a_cloudhook_github_was_not_told_about_is_dropped(
    hass: HomeAssistant,
    loaded_external: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    mock_route: dict[str, MagicMock],
) -> None:
    aioclient_mock.patch(f"{API}/app/hook/config", status=500, json={})
    result = await loaded_external.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ROUTE: ROUTE_CLOUD}
    )
    assert result["errors"] == {"base": "cannot_connect"}
    assert loaded_external.data[CONF_CLOUDHOOK] is False
    mock_route["delete"].assert_awaited_once_with(hass, WEBHOOK_ID)
    # The form is still open, so the flow keeps its hold until it closes.
    assert held(hass) == {WEBHOOK_ID: 1}
    hass.config_entries.flow.async_abort(result["flow_id"])
    assert held(hass) == {}


# --------------------------------------------------------------- options


async def test_options_reach_the_running_inbox(
    hass: HomeAssistant, loaded: MockConfigEntry
) -> None:
    async_mock_service(hass, "notify", "mobile_app_phone")
    async_mock_service(hass, "notify", "send_message")
    before = loaded.runtime_data
    result = await hass.config_entries.options.async_init(loaded.entry_id)
    assert result["step_id"] == "init"
    selector = next(
        v for k, v in result["data_schema"].schema.items() if k == CONF_NOTIFY_TARGETS
    )
    assert selector.config["options"] == ["mobile_app_phone"]
    me = next(v for k, v in result["data_schema"].schema.items() if k == CONF_ME)
    assert me.config["multiple"] is True
    defaults = {str(k): k.default() for k in result["data_schema"].schema}
    assert defaults[CONF_ME] == [ACCOUNT]
    new = {
        CONF_ME: ["HeidrickLA", " ", "Partner"],
        CONF_NOTIFY_TARGETS: ["mobile_app_phone"],
        CONF_PERSISTENT: False,
        CONF_NOTIFY_ON: [NOTIFY_OPENED],
        CONF_INCLUDE_BOTS: True,
        CONF_INCLUDE_OWN: True,
        CONF_NOTIFY_CI: False,
    }
    result = await hass.config_entries.options.async_configure(result["flow_id"], new)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done(wait_background_tasks=True)
    running = loaded.runtime_data
    assert running is not before
    assert dict(running.config_entry.options) == {
        **new,
        CONF_ME: ["heidrickla", "partner"],
    }
    assert running.me == {"heidrickla", "partner"}


async def test_blank_logins_mean_the_account(
    hass: HomeAssistant, loaded: MockConfigEntry
) -> None:
    result = await hass.config_entries.options.async_init(loaded.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_ME: [],
            CONF_PERSISTENT: False,
            CONF_NOTIFY_ON: [NOTIFY_OPENED],
            CONF_INCLUDE_BOTS: False,
            CONF_INCLUDE_OWN: False,
        },
    )
    assert loaded.options[CONF_ME] == [ACCOUNT]
