"""Default-branch Actions sync, webhook changes, permission repair and CI entities."""

from __future__ import annotations

import datetime as dt
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.github_inbox.api import (
    GitHubForbiddenError,
    GitHubNotFoundError,
    Listing,
)
from custom_components.github_inbox.const import (
    CONF_NOTIFY_CI,
    CONF_NOTIFY_TARGETS,
    CONF_PERSISTENT,
    DOMAIN,
    ISSUE_ACTIONS_PERMISSION,
    NOTIFY_DELAY,
)
from custom_components.github_inbox.coordinator import InboxCoordinator
from custom_components.github_inbox.websocket import build_snapshot
from tests.ha.conftest import (
    ACCOUNT,
    ACCOUNT_ID,
    OPTIONS,
    GitHub,
    entry_data,
    setup_entry,
)
from tests.payloads import REPO, user, workflow_run


async def ci_inbox(
    hass: HomeAssistant, pem: str, github: GitHub, runs: list[dict[str, Any]]
) -> InboxCoordinator:
    github.token()
    github.repos([REPO, "heidrickla/empty"], runs_by_repo={REPO: runs})
    github.issues([])
    github.issues([], repo="heidrickla/empty")
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=ACCOUNT,
        unique_id=str(ACCOUNT_ID),
        data=entry_data(pem),
        options={
            **OPTIONS,
            CONF_NOTIFY_TARGETS: ["mobile_app_phone"],
            CONF_PERSISTENT: True,
        },
    )
    return await setup_entry(hass, entry)


async def later(hass: HomeAssistant) -> None:
    async_fire_time_changed(
        hass, dt_util.utcnow() + dt.timedelta(seconds=NOTIFY_DELAY + 1)
    )
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_sync_builds_ci_and_websocket_shape(
    hass: HomeAssistant, pem: str, github: GitHub
) -> None:
    runs = [
        workflow_run(8),
        workflow_run(7, conclusion="failure"),
        workflow_run(9, branch="feature"),
        workflow_run(4, workflow_id=20, name="Build", conclusion="failure"),
    ]
    inbox = await ci_inbox(hass, pem, github, runs)
    assert {
        key: run["id"] for key, run in inbox.store.data["ci"][REPO]["workflows"].items()
    } == {"10": 8, "20": 4}
    assert inbox.store.data["ci"]["heidrickla/empty"]["workflows"] == {}
    assert inbox.data["failing_ci"] == 1
    state = hass.states.get("sensor.github_heidrickla_failing_ci")
    assert state.state == "1"
    assert state.attributes["repositories"] == [REPO]
    snap = build_snapshot(hass)
    assert snap["ci_totals"] == {"failing": 1, "running": 0, "passing": 0}
    assert snap["ci"][0]["repo"] == REPO
    assert snap["ci"][0]["state"] == "failing"
    assert [r["name"] for r in snap["ci"][0]["workflows"]] == ["Build", "Tests"]
    assert snap["ci"][1]["state"] == "no_ci"


@pytest.mark.parametrize("error", [GitHubForbiddenError, GitHubNotFoundError])
async def test_refused_ci_read_keeps_stored_runs(
    hass: HomeAssistant, pem: str, github: GitHub, error: type[Exception]
) -> None:
    inbox = await ci_inbox(hass, pem, github, [workflow_run(1, conclusion="failure")])
    before = inbox.store.data["ci"][REPO]["workflows"]
    real = inbox.client.workflow_runs

    async def refused(repo: str, branch: str) -> list[dict[str, Any]]:
        if repo == REPO:
            raise error("refused")
        return await real(repo, branch)

    with patch.object(inbox.client, "workflow_runs", side_effect=refused):
        await inbox.async_refresh()
    assert inbox.sync_state == "ok"
    assert inbox.store.data["ci"][REPO]["workflows"] == before
    assert inbox.store.data["ci"][REPO]["unreadable"] is True
    assert build_snapshot(hass)["ci"][0]["unreadable"] is True


async def test_partial_installation_listing_keeps_ci_only_repositories(
    hass: HomeAssistant, pem: str, github: GitHub
) -> None:
    inbox = await ci_inbox(hass, pem, github, [workflow_run(1, conclusion="failure")])
    with patch.object(
        inbox.client,
        "repositories",
        AsyncMock(return_value=Listing([], complete=False)),
    ):
        await inbox.async_refresh()
    assert REPO in inbox.store.data["repos"]
    assert inbox.store.data["ci"][REPO]["workflows"]["10"]["id"] == 1
    assert build_snapshot(hass)["ci"][0]["repo"] == REPO


async def test_webhook_uses_only_newer_default_branch_run(
    hass: HomeAssistant, pem: str, github: GitHub
) -> None:
    inbox = await ci_inbox(hass, pem, github, [workflow_run(10)])

    def receive(run: dict[str, Any], guid: str) -> None:
        inbox.async_receive(
            "workflow_run",
            guid,
            {
                "repository": {"full_name": REPO, "default_branch": "main"},
                "workflow_run": run,
                "sender": user("heidrickla"),
            },
        )

    receive(workflow_run(11, branch="feature", conclusion="failure"), "branch")
    receive(workflow_run(9, conclusion="failure"), "old")
    receive(
        workflow_run(10, updated="2026-10-30T09:00:00Z", conclusion="failure"),
        "older-time",
    )
    assert inbox.store.data["ci"][REPO]["workflows"]["10"]["conclusion"] == "success"
    receive(
        workflow_run(10, updated="2026-10-30T11:00:00Z", conclusion="failure"),
        "newer-time",
    )
    assert inbox.store.data["ci"][REPO]["workflows"]["10"]["conclusion"] == "failure"
    receive(workflow_run(11, conclusion="success"), "newer-id")
    assert inbox.store.data["ci"][REPO]["workflows"]["10"]["id"] == 11


async def test_sync_does_not_replace_a_newer_webhook_run_with_an_older_api_run(
    hass: HomeAssistant, pem: str, github: GitHub
) -> None:
    inbox = await ci_inbox(hass, pem, github, [workflow_run(10)])
    inbox.async_receive(
        "workflow_run",
        "new",
        {
            "repository": {"full_name": REPO, "default_branch": "main"},
            "workflow_run": workflow_run(11, conclusion="failure"),
        },
    )
    await inbox.async_refresh()
    assert inbox.store.data["ci"][REPO]["workflows"]["10"]["id"] == 11
    assert inbox.data["failing_ci"] == 1


async def test_permission_repair_appears_and_clears_on_next_sync(
    hass: HomeAssistant, pem: str, github: GitHub
) -> None:
    inbox = await ci_inbox(hass, pem, github, [])
    issue_id = f"{ISSUE_ACTIONS_PERMISSION}_{inbox.config_entry.entry_id}"
    with patch.object(
        inbox.client.app,
        "create_token",
        AsyncMock(return_value=("token", 9999999999.0, {"issues": "read"})),
    ):
        await inbox.async_refresh()
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.translation_key == ISSUE_ACTIONS_PERMISSION
    assert "settings/apps/" in issue.translation_placeholders["settings_url"]
    assert (
        "settings/installations/" in issue.translation_placeholders["installation_url"]
    )
    assert build_snapshot(hass)["ci_permissions"][0]["account"] == ACCOUNT
    with patch.object(
        inbox.client.app,
        "create_token",
        AsyncMock(return_value=("token", 9999999999.0, {"actions": "read"})),
    ):
        await inbox.async_refresh()
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
    assert build_snapshot(hass)["ci_permissions"] == []


async def test_missing_actions_permission_is_reported_at_setup(
    hass: HomeAssistant, pem: str, github: GitHub
) -> None:
    github.token(permissions={"issues": "read"})
    github.repos([REPO])
    github.issues([])
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=ACCOUNT,
        unique_id=str(ACCOUNT_ID),
        data=entry_data(pem),
        options=dict(OPTIONS),
    )
    inbox = await setup_entry(hass, entry)
    issue_id = f"{ISSUE_ACTIONS_PERMISSION}_{entry.entry_id}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None
    assert inbox.store.data["ci"][REPO]["unreadable"] is True
    assert build_snapshot(hass)["ci_permissions"][0]["account"] == ACCOUNT


async def test_ci_notifications_fail_recover_repeat_and_option(
    hass: HomeAssistant, pem: str, github: GitHub
) -> None:
    phone = async_mock_service(hass, "notify", "mobile_app_phone")
    inbox = await ci_inbox(hass, pem, github, [workflow_run(1)])

    def receive(run: dict[str, Any], guid: str) -> None:
        inbox.async_receive(
            "workflow_run",
            guid,
            {
                "repository": {"full_name": REPO, "default_branch": "main"},
                "workflow_run": run,
                "sender": user("renovate[bot]"),
            },
        )

    receive(workflow_run(2, conclusion="failure"), "fail")
    receive(workflow_run(2, conclusion="failure"), "repeat")
    await later(hass)
    assert len(phone) == 1
    assert REPO in phone[0].data["title"]
    assert phone[0].data["data"]["actions"][0]["uri"].endswith("/actions/runs/2")
    receive(workflow_run(3), "recover")
    await later(hass)
    assert len(phone) == 2
    assert "recovered" in phone[1].data["title"]
    assert "/actions/runs/3" in str(
        persistent_notification._async_get_or_create_notifications(hass)
    )
    entry = inbox.config_entry
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_NOTIFY_CI: False}
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    inbox = entry.runtime_data
    receive(workflow_run(4, conclusion="failure"), "muted")
    await later(hass)
    assert len(phone) == 2


@pytest.mark.parametrize("path", ["webhook", "sync"])
@pytest.mark.parametrize(
    ("middle", "final", "expected"),
    [
        ("running", "passing", ["ci_failed", "ci_recovered"]),
        ("running", "failing", ["ci_failed"]),
        ("cancelled", "passing", ["ci_failed", "ci_recovered"]),
    ],
)
async def test_ci_settled_notifications_across_unsettled_runs(
    hass: HomeAssistant,
    pem: str,
    github: GitHub,
    path: str,
    middle: str,
    final: str,
    expected: list[str],
) -> None:
    phone = async_mock_service(hass, "notify", "mobile_app_phone")
    inbox = await ci_inbox(hass, pem, github, [workflow_run(1)])

    async def advance(run: dict[str, Any], guid: str) -> None:
        if path == "webhook":
            inbox.async_receive(
                "workflow_run",
                guid,
                {
                    "repository": {"full_name": REPO, "default_branch": "main"},
                    "workflow_run": run,
                },
            )
        else:

            async def runs(repo: str, branch: str) -> list[dict[str, Any]]:
                return [run] if repo == REPO else []

            with patch.object(inbox.client, "workflow_runs", side_effect=runs):
                await inbox.async_refresh()
        await later(hass)

    await advance(workflow_run(2, conclusion="failure"), "failed")
    assert len(phone) == 1
    assert inbox.store.data["ci"][REPO]["settled"] == "failing"
    assert inbox.store.data["ci"][REPO]["failed"] == ["Tests"]

    await advance(
        workflow_run(
            3,
            status="in_progress" if middle == "running" else "completed",
            conclusion=None if middle == "running" else "cancelled",
        ),
        middle,
    )
    assert len(phone) == 1
    assert inbox.store.data["ci"][REPO]["settled"] == "failing"
    assert inbox.store.data["ci"][REPO]["failed"] == ["Tests"]

    await advance(
        workflow_run(4, conclusion="success" if final == "passing" else "failure"),
        final,
    )
    assert len(phone) == len(expected)
    assert [
        "ci_recovered" if "recovered" in call.data["title"] else "ci_failed"
        for call in phone
    ] == expected
    assert inbox.store.data["ci"][REPO]["settled"] == final
    assert inbox.store.data["ci"][REPO]["failed"] == (
        [] if final == "passing" else ["Tests"]
    )
    if final == "passing":
        assert "Tests" in phone[-1].data["title"]
        assert phone[-1].data["data"]["actions"][0]["uri"].endswith("/actions/runs/4")


async def test_first_sync_of_failing_repository_sets_settled_without_notice(
    hass: HomeAssistant, pem: str, github: GitHub
) -> None:
    phone = async_mock_service(hass, "notify", "mobile_app_phone")
    inbox = await ci_inbox(hass, pem, github, [workflow_run(1, conclusion="failure")])
    await later(hass)
    assert phone == []
    assert inbox.store.data["ci"][REPO]["settled"] == "failing"
    assert inbox.store.data["ci"][REPO]["failed"] == ["Tests"]


async def test_existing_ci_entry_without_settled_starts_without_notice(
    hass: HomeAssistant, pem: str, github: GitHub
) -> None:
    phone = async_mock_service(hass, "notify", "mobile_app_phone")
    inbox = await ci_inbox(hass, pem, github, [workflow_run(1)])
    entry = inbox.store.data["ci"][REPO]
    entry.pop("settled")
    entry.pop("failed")

    inbox.async_receive(
        "workflow_run",
        "first-after-upgrade",
        {
            "repository": {"full_name": REPO, "default_branch": "main"},
            "workflow_run": workflow_run(2, conclusion="failure"),
        },
    )
    await later(hass)
    assert phone == []
    assert inbox.store.data["ci"][REPO]["settled"] == "failing"
    assert inbox.store.data["ci"][REPO]["failed"] == ["Tests"]
