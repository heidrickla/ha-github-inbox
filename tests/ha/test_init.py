"""Setup, the sync, unload and removal."""

from __future__ import annotations

import datetime as dt
from unittest.mock import MagicMock

import pytest
from homeassistant.components import frontend, persistent_notification, webhook
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.github_inbox.const import (
    CONF_CLOUDHOOK,
    CONF_PERSISTENT,
    CONF_WEBHOOK_ID,
    DOMAIN,
    ISSUE_WEBHOOK_ROUTE,
    PANEL_URL,
    SYNC_RETRY,
)
from tests.ha.conftest import (
    ACCOUNT,
    ACCOUNT_ID,
    OPTIONS,
    WEBHOOK_ID,
    GitHub,
    entry_data,
    setup_entry,
)
from tests.payloads import REPO, comment, issue, pull, review

ISSUES = "sensor.github_heidrickla_open_issues"
PRS = "sensor.github_heidrickla_open_pull_requests"
UNSEEN = "sensor.github_heidrickla_unseen"


def seed(github: GitHub) -> None:
    github.token()
    github.repos([REPO, "heidrickla/empty"])
    github.issues([issue(2, comments=1), issue(5, pull_request=True)])
    github.issues([], repo="heidrickla/empty")
    github.comments(2, [comment(11)])
    github.pull(5, pull(5), reviews=[review(7, "APPROVED")])


async def test_setup_syncs_every_open_item(
    hass: HomeAssistant, config_entry: MockConfigEntry, github: GitHub
) -> None:
    seed(github)
    coordinator = await setup_entry(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED
    assert set(coordinator.items) == {f"{REPO}#2", f"{REPO}#5"}
    assert [e["id"] for e in coordinator.items[f"{REPO}#2"]["timeline"]] == ["c11"]
    pr = coordinator.items[f"{REPO}#5"]
    assert pr["pr"]["additions"] == 12
    assert [e["id"] for e in pr["timeline"]] == ["r7"]
    assert coordinator.sync_state == "ok"
    assert hass.states.get(ISSUES).state == "1"
    assert hass.states.get(PRS).state == "1"
    assert hass.states.get(UNSEEN).state == "2"
    assert frontend.async_panel_exists(hass, PANEL_URL)
    assert coordinator.store.data["repos"] == ["heidrickla/empty", REPO]


async def test_a_second_sync_reads_only_what_changed(
    hass: HomeAssistant, config_entry: MockConfigEntry, github: GitHub
) -> None:
    seed(github)
    coordinator = await setup_entry(hass, config_entry)
    before = github.calls("get", f"/repos/{REPO}/issues/2/comments")
    await coordinator.async_refresh()
    assert github.calls("get", f"/repos/{REPO}/issues/2/comments") == before
    assert coordinator.last_update_success


async def test_a_sync_after_downtime_notifies_once_for_what_it_missed(
    hass: HomeAssistant, pem: str, github: GitHub
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=ACCOUNT,
        unique_id=str(ACCOUNT_ID),
        data=entry_data(pem),
        options={**OPTIONS, CONF_PERSISTENT: True},
    )
    seed(github)
    coordinator = await setup_entry(hass, entry)
    assert not persistent_notification._async_get_or_create_notifications(hass)
    github.reset()
    github.token()
    github.repos([REPO])
    later = "2099-01-01T00:00:00Z"
    github.issues(
        [issue(2, comments=2, updated=later), issue(9, created=later, updated=later)]
    )
    github.comments(2, [comment(11), comment(12, at=later, body_html="<p>Ping</p>")])
    await coordinator.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert set(coordinator.items) == {f"{REPO}#2", f"{REPO}#9"}
    types = [a["type"] for a in coordinator.store.data["activity"]]
    assert sorted(types) == ["commented", "opened"]
    notes = persistent_notification._async_get_or_create_notifications(hass)
    (note,) = notes.values()
    assert (
        note["title"]
        == "2 updates in your repositories while Home Assistant was not listening"
    )


async def test_an_unreadable_repository_has_no_items(
    hass: HomeAssistant, config_entry: MockConfigEntry, github: GitHub
) -> None:
    github.token()
    github.repos([REPO])
    github.issues([], status=404)
    coordinator = await setup_entry(hass, config_entry)
    assert coordinator.items == {}
    assert coordinator.sync_state == "ok"


async def test_a_refused_key_starts_reauth(
    hass: HomeAssistant, config_entry: MockConfigEntry, github: GitHub
) -> None:
    github.token(status=401)
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["source"] for f in flows] == [SOURCE_REAUTH]


async def test_github_unreachable_at_setup_retries(
    hass: HomeAssistant, config_entry: MockConfigEntry, github: GitHub
) -> None:
    github.token(status=502)
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_a_failed_sync_makes_entities_unavailable_and_retries(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    github: GitHub,
    caplog: pytest.LogCaptureFixture,
) -> None:
    github.token()
    github.repos(status=500)
    coordinator = await setup_entry(hass, config_entry)
    assert not coordinator.last_update_success
    assert coordinator.sync_state == "failed"
    assert hass.states.get(ISSUES).state == STATE_UNAVAILABLE
    assert caplog.text.count("Error fetching") == 1
    github.reset()
    seed(github)
    async_fire_time_changed(
        hass, dt_util.utcnow() + dt.timedelta(seconds=SYNC_RETRY[0] + 1)
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert coordinator.last_update_success
    assert hass.states.get(ISSUES).state == "1"


async def test_a_sync_refused_for_auth_starts_reauth(
    hass: HomeAssistant, config_entry: MockConfigEntry, github: GitHub
) -> None:
    github.token()
    github.repos(status=401)
    await setup_entry(hass, config_entry)
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["source"] for f in flows] == [SOURCE_REAUTH]


async def test_cloud_reconnect_resyncs(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    github: GitHub,
    mock_route: dict[str, MagicMock],
) -> None:
    seed(github)
    coordinator = await setup_entry(hass, config_entry)
    ((_hass, on_connected),) = [c.args for c in mock_route["listen"].call_args_list]
    assert on_connected == coordinator.schedule_sync
    before = github.calls("get", "/installation/repositories")
    on_connected()
    async_fire_time_changed(hass, dt_util.utcnow() + dt.timedelta(seconds=3))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert github.calls("get", "/installation/repositories") == before + 1


async def test_a_cloud_route_signed_out_is_a_repair_until_signed_in(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    github: GitHub,
    mock_route: dict[str, MagicMock],
) -> None:
    seed(github)
    mock_route["signed_in"].return_value = False
    await setup_entry(hass, config_entry)
    registry = ir.async_get(hass)
    issue_id = f"{ISSUE_WEBHOOK_ROUTE}_{config_entry.entry_id}"
    assert registry.async_get_issue(DOMAIN, issue_id)
    mock_route["signed_in"].return_value = True
    await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert not registry.async_get_issue(DOMAIN, issue_id)
    assert await hass.config_entries.async_remove(config_entry.entry_id)


async def test_an_external_route_without_a_public_url_is_a_repair(
    hass: HomeAssistant, pem: str, github: GitHub, mock_route: dict[str, MagicMock]
) -> None:
    seed(github)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=ACCOUNT,
        unique_id=str(ACCOUNT_ID),
        data=entry_data(pem, **{CONF_CLOUDHOOK: False}),
        options=OPTIONS,
    )
    await setup_entry(hass, entry)
    assert ir.async_get(hass).async_get_issue(
        DOMAIN, f"{ISSUE_WEBHOOK_ROUTE}_{entry.entry_id}"
    )
    mock_route["external"].assert_called_with(hass, WEBHOOK_ID)
    mock_route["listen"].assert_not_called()
    assert await hass.config_entries.async_remove(entry.entry_id)
    mock_route["delete"].assert_not_called()


async def test_a_taken_webhook_id_retries(
    hass: HomeAssistant, config_entry: MockConfigEntry, github: GitHub
) -> None:
    seed(github)
    webhook.async_register(hass, "other", "other", WEBHOOK_ID, MagicMock())
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_unload_and_remove(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    github: GitHub,
    mock_route: dict[str, MagicMock],
) -> None:
    seed(github)
    await setup_entry(hass, config_entry)
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.NOT_LOADED
    assert not frontend.async_panel_exists(hass, PANEL_URL)
    assert WEBHOOK_ID not in hass.data.get("webhook", {})
    assert await hass.config_entries.async_remove(config_entry.entry_id)
    mock_route["delete"].assert_awaited_once_with(hass, WEBHOOK_ID)


async def test_the_panel_stays_while_another_inbox_is_loaded(
    hass: HomeAssistant, config_entry: MockConfigEntry, pem: str, github: GitHub
) -> None:
    seed(github)
    await setup_entry(hass, config_entry)
    other = MockConfigEntry(
        domain=DOMAIN,
        title="org",
        unique_id="99",
        data=entry_data(pem, **{CONF_WEBHOOK_ID: "hook-2"}),
        options=OPTIONS,
    )
    await setup_entry(hass, other)
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    assert frontend.async_panel_exists(hass, PANEL_URL)
