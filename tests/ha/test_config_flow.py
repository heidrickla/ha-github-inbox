"""The config flow: app creation through GitHub's pages, installation, reauth, reconfigure and options."""

from __future__ import annotations

import html
import json
import re
from typing import Any
from unittest.mock import MagicMock

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
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
    CONF_APP_ID,
    CONF_APP_NAME,
    CONF_CLOUDHOOK,
    CONF_INCLUDE_BOTS,
    CONF_INCLUDE_OWN,
    CONF_NOTIFY_ON,
    CONF_NOTIFY_TARGETS,
    CONF_ORGANIZATION,
    CONF_PERSISTENT,
    CONF_PRIVATE_KEY,
    CONF_ROUTE,
    CONF_WEBHOOK_ID,
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
    setup_entry,
)
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
        {CONF_APP_NAME: "HA Inbox test", CONF_ROUTE: ROUTE_CLOUD, **user},
    )


async def through_github(hass: HomeAssistant, client: Any, result: Any) -> Any:
    """The browser's trip through GitHub: the manifest page, creation, installation."""
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
    assert result["url"] == f"{APP_URL}/installations/new"
    resp = await client.get(f"{views.INSTALLED_PATH}?installation_id={INSTALLATION_ID}")
    assert resp.status == 200
    assert "finishes the setup" in await resp.text()
    return await hass.config_entries.flow.async_configure(result["flow_id"])


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
    assert data[CONF_CLOUDHOOK] is True
    assert data["installation_id"] == INSTALLATION_ID
    assert data["account_id"] == ACCOUNT_ID
    assert result["options"][CONF_PERSISTENT] is True
    assert result["result"].unique_id == str(ACCOUNT_ID)
    assert mock_route["hook"].await_args.args[1:] == (
        data[CONF_WEBHOOK_ID],
        ROUTE_CLOUD,
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    mock_route["delete"].assert_not_called()
    assert views.pending(hass) == {}


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
        result["flow_id"], {CONF_APP_NAME: "HA Inbox test", CONF_ROUTE: ROUTE_EXTERNAL}
    )
    assert result["errors"] == {CONF_ROUTE: "route_unavailable"}


async def test_a_cloudhook_that_cannot_be_made(
    hass: HomeAssistant, mock_route: dict[str, MagicMock]
) -> None:
    mock_route["hook"].side_effect = route.RouteUnavailableError("cloud")
    result = await start(hass)
    assert result["errors"] == {CONF_ROUTE: "route_unavailable"}


async def test_no_route_aborts(
    hass: HomeAssistant, mock_route: dict[str, MagicMock]
) -> None:
    mock_route["ready"].return_value = False
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_route"


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
    hass.config_entries.flow.async_abort(result["flow_id"])
    await hass.async_block_till_done()
    webhook_id = mock_route["hook"].await_args.args[1]
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
        result["flow_id"], {CONF_APP_NAME: "HA Inbox again", CONF_ROUTE: ROUTE_CLOUD}
    )
    assert mock_route["hook"].await_args.args[1:] == (WEBHOOK_ID, ROUTE_CLOUD)
    client = await hass_client_no_auth()
    result = await through_github(hass, client, result)
    assert result["reason"] == "reauth_successful"
    await hass.async_block_till_done(wait_background_tasks=True)
    assert loaded.data[CONF_APP_ID] == NEW_APP_ID
    assert loaded.data[CONF_WEBHOOK_ID] == WEBHOOK_ID
    mock_route["delete"].assert_not_called()


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
    new = {
        CONF_NOTIFY_TARGETS: ["mobile_app_phone"],
        CONF_PERSISTENT: False,
        CONF_NOTIFY_ON: [NOTIFY_OPENED],
        CONF_INCLUDE_BOTS: True,
        CONF_INCLUDE_OWN: True,
    }
    result = await hass.config_entries.options.async_configure(result["flow_id"], new)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done(wait_background_tasks=True)
    running = loaded.runtime_data
    assert running is not before
    assert dict(running.config_entry.options) == new
