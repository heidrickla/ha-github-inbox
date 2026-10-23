"""Setup, the sync, unload and removal."""

from __future__ import annotations

import asyncio
import datetime as dt
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.components import frontend, persistent_notification, webhook
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import (
    EVENT_CORE_CONFIG_UPDATE,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.github_inbox.api import Listing
from custom_components.github_inbox.const import (
    CONF_CLOUDHOOK,
    CONF_PERSISTENT,
    CONF_WEBHOOK_ID,
    DOMAIN,
    ISSUE_WEBHOOK_ROUTE,
    PANEL_URL,
    SIGNAL_UPDATED,
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
from tests.payloads import REPO, comment, issue, pull, review, user

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


async def test_setup_sweeps_cloudhooks_left_by_a_restart(
    hass: HomeAssistant, config_entry: MockConfigEntry, github: GitHub
) -> None:
    seed(github)
    with patch(
        "custom_components.github_inbox.route.async_sweep_when_connected"
    ) as sweep:
        await setup_entry(hass, config_entry)
    sweep.assert_called_once_with(hass)


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


async def test_a_sync_does_not_notify_history_it_had_not_seen(
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
    github.reset()
    github.token()
    # A repository added while Home Assistant was down, with old items, and a review newer than the last sync.
    github.repos([REPO, "heidrickla/added"])
    github.comments(2, [comment(11)])
    later = "2099-01-01T00:00:00Z"
    github.issues(
        [issue(1, created="2026-01-01T00:00:00Z", comments=1, updated=later)],
        repo="heidrickla/added",
    )
    github.comments(1, [comment(3, at="2026-01-02T00:00:00Z")], repo="heidrickla/added")
    github.issues(
        [issue(2, comments=1), issue(5, pull_request=True, updated=later)],
    )
    github.pull(
        5,
        pull(5, updated=later),
        reviews=[review(7, "APPROVED"), review(8, "CHANGES_REQUESTED", at=later)],
    )
    await coordinator.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert "heidrickla/added#1" in coordinator.items
    activity = coordinator.store.data["activity"]
    assert [(a["key"], a["type"], a["state"]) for a in activity] == [
        (f"{REPO}#5", "reviewed", "changes_requested")
    ]
    (note,) = persistent_notification._async_get_or_create_notifications(hass).values()
    assert note["title"] == "reviewer requested changes on ha-glkvm#5"


async def test_archived_repositories_are_not_read(
    hass: HomeAssistant, config_entry: MockConfigEntry, github: GitHub
) -> None:
    github.token()
    github.repos([REPO], archived=["heidrickla/old"])
    github.issues([])
    github.issues([issue(4)], repo="heidrickla/old")
    coordinator = await setup_entry(hass, config_entry)
    assert coordinator.items == {}
    assert coordinator.store.data["repos"] == [REPO]
    assert github.calls("get", "/repos/heidrickla/old/issues") == 0


async def test_a_body_the_sanitiser_cannot_parse_does_not_stop_the_sync(
    hass: HomeAssistant, config_entry: MockConfigEntry, github: GitHub
) -> None:
    github.token()
    github.repos([REPO])
    github.issues(
        [issue(2, body="1. x", body_html='<ol start="\u00b2"><li>x</li></ol>')]
    )
    github.comments(2, [])
    coordinator = await setup_entry(hass, config_entry)
    assert coordinator.sync_state == "ok"
    assert coordinator.items[f"{REPO}#2"]["body_html"] == "<ol><li>x</li></ol>"
    with patch(
        "custom_components.github_inbox.sanitize._Sanitiser.feed",
        side_effect=AssertionError("parser broke"),
    ):
        await coordinator.async_refresh()
    assert coordinator.last_update_success


async def test_a_refused_listing_keeps_the_stored_items(
    hass: HomeAssistant, config_entry: MockConfigEntry, github: GitHub
) -> None:
    seed(github)
    coordinator = await setup_entry(hass, config_entry)
    github.reset()
    github.token()
    github.repos([REPO, "heidrickla/empty"])
    github.issues([], status=403)
    github.issues([], repo="heidrickla/empty")
    await coordinator.async_refresh()
    assert coordinator.last_update_success
    assert set(coordinator.items) == {f"{REPO}#2", f"{REPO}#5"}
    assert REPO in coordinator.store.data["repos"]


async def test_a_rate_limited_listing_fails_the_sync(
    hass: HomeAssistant, config_entry: MockConfigEntry, github: GitHub
) -> None:
    seed(github)
    coordinator = await setup_entry(hass, config_entry)
    github.reset()
    github.token()
    github.repos([REPO])
    github.m.get(
        f"https://api.github.com/repos/{REPO}/issues",
        status=403,
        json={"message": "You have exceeded a secondary rate limit"},
    )
    await coordinator.async_refresh()
    assert not coordinator.last_update_success
    assert set(coordinator.items) == {f"{REPO}#2", f"{REPO}#5"}


async def test_listings_cut_at_the_page_cap_keep_the_rest(
    hass: HomeAssistant, config_entry: MockConfigEntry, github: GitHub
) -> None:
    seed(github)
    coordinator = await setup_entry(hass, config_entry)
    coordinator.async_mark_seen(None)
    real = coordinator.client.open_issues

    async def cut(repo: str) -> Listing:
        listing = await real(repo)
        return Listing(listing.rows[:1], complete=False)

    with patch.object(coordinator.client, "open_issues", cut):
        await coordinator.async_refresh()
    assert set(coordinator.items) == {f"{REPO}#2", f"{REPO}#5"}

    async def few(*_args: Any, **_kwargs: Any) -> Listing:
        return Listing([{"full_name": "heidrickla/empty"}], complete=False)

    with patch.object(coordinator.client, "repositories", few):
        await coordinator.async_refresh()
    assert set(coordinator.items) == {f"{REPO}#2", f"{REPO}#5"}
    assert set(coordinator.store.data["seen"]) == {f"{REPO}#2", f"{REPO}#5"}


async def test_a_sync_drops_the_seen_marks_of_items_it_removes(
    hass: HomeAssistant, config_entry: MockConfigEntry, github: GitHub
) -> None:
    seed(github)
    coordinator = await setup_entry(hass, config_entry)
    coordinator.async_mark_seen(None)
    github.reset()
    github.token()
    github.repos([REPO])
    github.issues([issue(5, pull_request=True)])
    await coordinator.async_refresh()
    assert set(coordinator.store.data["seen"]) == {f"{REPO}#5"}


async def test_webhooks_during_a_sync_win_over_its_listing(
    hass: HomeAssistant, config_entry: MockConfigEntry, github: GitHub
) -> None:
    github.token()
    github.repos([REPO])
    github.issues([issue(2, comments=1), issue(3)])
    github.comments(2, [comment(11)])
    coordinator = await setup_entry(hass, config_entry)
    github.reset()
    github.token()
    github.repos([REPO])
    github.issues([issue(2, comments=1), issue(3, title="Stale")])
    github.comments(2, [comment(11)])
    real = coordinator.client.open_issues

    async def racing(repo: str) -> Listing:
        listing = await real(repo)
        base = {"repository": {"full_name": REPO}, "sender": user("someone")}
        closed = issue(2, state="closed")
        coordinator.async_receive(
            "issues", "x1", {**base, "action": "closed", "issue": closed}
        )
        edited = issue(3, title="Newest", updated="2099-01-01T00:00:00Z")
        coordinator.async_receive(
            "issues", "x2", {**base, "action": "edited", "issue": edited}
        )
        new = issue(4, title="Brand new")
        coordinator.async_receive(
            "issues", "x3", {**base, "action": "opened", "issue": new}
        )
        return listing

    with patch.object(coordinator.client, "open_issues", racing):
        await coordinator.async_refresh()
    assert f"{REPO}#2" not in coordinator.items
    assert coordinator.items[f"{REPO}#3"]["title"] == "Newest"
    assert coordinator.items[f"{REPO}#4"]["title"] == "Brand new"


async def test_counts_are_unknown_until_the_first_sync(
    hass: HomeAssistant, config_entry: MockConfigEntry, github: GitHub
) -> None:
    seed(github)
    release = asyncio.Event()

    async def held(self: Any) -> Listing:
        await release.wait()
        return Listing([{"full_name": REPO}], complete=True)

    with patch(
        "custom_components.github_inbox.api.InstallationClient.repositories", held
    ):
        config_entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
        assert hass.states.get(ISSUES).state == STATE_UNKNOWN
        assert hass.states.get(UNSEEN).state == STATE_UNKNOWN
        release.set()
        await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get(ISSUES).state == "1"


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
    # Each failure waits longer: 5, 15, then 60 minutes, and stays at 60.
    for delay in (*SYNC_RETRY, SYNC_RETRY[-1]):
        before = github.calls("get", "/installation/repositories")
        async_fire_time_changed(
            hass, dt_util.utcnow() + dt.timedelta(seconds=delay - 5)
        )
        await hass.async_block_till_done(wait_background_tasks=True)
        assert github.calls("get", "/installation/repositories") == before
        async_fire_time_changed(
            hass, dt_util.utcnow() + dt.timedelta(seconds=delay + 1)
        )
        await hass.async_block_till_done(wait_background_tasks=True)
        assert github.calls("get", "/installation/repositories") == before + 1
    assert caplog.text.count("Error fetching") == 1
    github.reset()
    seed(github)
    async_fire_time_changed(
        hass, dt_util.utcnow() + dt.timedelta(seconds=SYNC_RETRY[-1] + 1)
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert coordinator.last_update_success
    assert hass.states.get(ISSUES).state == "1"


async def test_a_sync_refused_for_auth_starts_reauth(
    hass: HomeAssistant, config_entry: MockConfigEntry, github: GitHub
) -> None:
    github.token()
    github.repos(status=401)
    states: list[str] = []
    config_entry.add_to_hass(hass)
    unsub = async_dispatcher_connect(
        hass,
        SIGNAL_UPDATED,
        lambda: states.append(config_entry.runtime_data.sync_state),
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    unsub()
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["source"] for f in flows] == [SOURCE_REAUTH]
    # The panel hears the failure, not only the start of the sync.
    assert states[-1] == "failed"


async def test_cloud_changes_resync_and_recheck_the_route(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    github: GitHub,
    mock_route: dict[str, MagicMock],
) -> None:
    seed(github)
    await setup_entry(hass, config_entry)
    ((_hass, on_change),) = [c.args for c in mock_route["listen"].call_args_list]
    registry = ir.async_get(hass)
    issue_id = f"{ISSUE_WEBHOOK_ROUTE}_{config_entry.entry_id}"
    before = github.calls("get", "/installation/repositories")
    # Signed out: the connection drops and the repair appears without a reload.
    mock_route["signed_in"].return_value = False
    on_change(False)
    assert registry.async_get_issue(DOMAIN, issue_id)
    async_fire_time_changed(hass, dt_util.utcnow() + dt.timedelta(seconds=3))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert github.calls("get", "/installation/repositories") == before
    mock_route["signed_in"].return_value = True
    on_change(True)
    assert not registry.async_get_issue(DOMAIN, issue_id)
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
    issue_id = f"{ISSUE_WEBHOOK_ROUTE}_{entry.entry_id}"
    mock_route["external"].return_value = "https://ha.example/api/webhook/x"
    hass.bus.async_fire(EVENT_CORE_CONFIG_UPDATE, {})
    await hass.async_block_till_done()
    assert not ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    mock_route["external"].return_value = None
    hass.bus.async_fire(EVENT_CORE_CONFIG_UPDATE, {})
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert await hass.config_entries.async_remove(entry.entry_id)
    mock_route["delete"].assert_not_called()
    assert not ir.async_get(hass).async_get_issue(DOMAIN, issue_id)


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
    hass_storage: dict[str, Any],
) -> None:
    seed(github)
    coordinator = await setup_entry(hass, config_entry)
    store_key = f"{DOMAIN}.{config_entry.entry_id}"
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.NOT_LOADED
    assert store_key in hass_storage
    assert not frontend.async_panel_exists(hass, PANEL_URL)
    assert WEBHOOK_ID not in hass.data.get("webhook", {})
    # A refresh that lands after unload does not schedule another write.
    with patch.object(coordinator.store, "async_save") as save:
        coordinator._publish()
    save.assert_not_called()
    assert await hass.config_entries.async_remove(config_entry.entry_id)
    mock_route["delete"].assert_awaited_once_with(hass, WEBHOOK_ID)
    assert store_key not in hass_storage


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
