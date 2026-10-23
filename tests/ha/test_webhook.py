"""Webhook deliveries: the signature gate, each event's effect on the inbox, the per-item refresh and notifications."""

from __future__ import annotations

import asyncio
import datetime as dt
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.components import persistent_notification
from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.github_inbox import coordinator as coordinator_module
from custom_components.github_inbox import webhook
from custom_components.github_inbox.const import (
    ACTIVITY_DAYS,
    ACTIVITY_KEEP,
    CONF_INCLUDE_BOTS,
    CONF_INCLUDE_OWN,
    CONF_ME,
    CONF_NOTIFY_ON,
    CONF_NOTIFY_TARGETS,
    CONF_PERSISTENT,
    DELIVERY_KEEP,
    DOMAIN,
    NOTIFY_CLOSED,
    NOTIFY_DELAY,
    REFRESH_DELAY,
)
from custom_components.github_inbox.coordinator import InboxCoordinator
from tests.ha.conftest import (
    ACCOUNT,
    ACCOUNT_ID,
    INSTALLATION_ID,
    OPTIONS,
    SECRET,
    WEBHOOK_ID,
    GitHub,
    deliver,
    entry_data,
    setup_entry,
)
from tests.payloads import (
    REPO,
    comment,
    issue,
    pull,
    review,
    review_comment,
    short_pull,
    user,
)

ISSUES = "sensor.github_heidrickla_open_issues"
EVENT = "event.github_heidrickla_activity"
KEY = f"{REPO}#2"
PR_KEY = f"{REPO}#5"


def repo_obj(name: str = REPO) -> dict[str, Any]:
    return {"full_name": name, "name": name.split("/")[1]}


def installation(
    action: str, number: int = INSTALLATION_ID, account: int = ACCOUNT_ID
) -> dict[str, Any]:
    return {
        "action": action,
        "installation": {"id": number, "account": {"login": ACCOUNT, "id": account}},
        "sender": user(ACCOUNT),
    }


def event(action: str, sender: str = "someone", **body: Any) -> dict[str, Any]:
    return {"action": action, "repository": repo_obj(), "sender": user(sender), **body}


async def later(hass: HomeAssistant, seconds: float) -> None:
    async_fire_time_changed(hass, dt_util.utcnow() + dt.timedelta(seconds=seconds))
    await hass.async_block_till_done(wait_background_tasks=True)


@pytest.fixture
async def inbox(hass: HomeAssistant, pem: str, github: GitHub) -> InboxCoordinator:
    """An empty inbox that notifies through a phone and a persistent notification."""
    github.token()
    github.repos([REPO])
    github.issues([])
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


@pytest.fixture
def phone(hass: HomeAssistant) -> list[ServiceCall]:
    return async_mock_service(hass, "notify", "mobile_app_phone")


@pytest.fixture
async def client(hass: HomeAssistant, hass_client_no_auth: Any) -> Any:
    return await hass_client_no_auth()


def note(hass: HomeAssistant) -> dict[str, Any] | None:
    """The newest persistent notification."""
    notes = persistent_notification._async_get_or_create_notifications(hass)
    return next(reversed(notes.values()), None)


# ------------------------------------------------------------------ gate


def test_signature() -> None:
    body = b'{"a":1}'
    import hashlib
    import hmac

    good = "sha256=" + hmac.new(b"k", body, hashlib.sha256).hexdigest()
    assert webhook.signature_valid("k", body, good)
    assert not webhook.signature_valid("k", body + b" ", good)
    assert not webhook.signature_valid("other", body, good)
    assert not webhook.signature_valid("k", body, None)
    assert not webhook.signature_valid("k", body, good.removeprefix("sha256="))


async def test_a_delivery_signed_with_another_secret_is_refused(
    hass: HomeAssistant, inbox: InboxCoordinator, client: Any
) -> None:
    resp = await deliver(
        client, "issues", event("opened", issue=issue(2)), secret="wrong"
    )
    assert resp.status == 401
    assert inbox.items == {}
    assert inbox.store.data["last_delivery"] is None


async def test_unsigned_and_malformed_bodies(
    hass: HomeAssistant, inbox: InboxCoordinator, client: Any
) -> None:
    resp = await client.post(f"/api/webhook/{WEBHOOK_ID}", data=b"{}")
    assert resp.status == 401
    import hashlib
    import hmac

    for body in (b"not json", b"[1, 2]"):
        sig = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
        resp = await client.post(
            f"/api/webhook/{WEBHOOK_ID}",
            data=body,
            headers={"X-Hub-Signature-256": sig},
        )
        assert resp.status == 400


async def test_a_delivery_for_an_unloaded_entry_is_not_found(
    hass: HomeAssistant,
) -> None:
    resp = await webhook._handle(hass, "nobody", None)  # type: ignore[arg-type]
    assert resp.status == 404


async def test_a_delivery_is_applied_once(
    hass: HomeAssistant, inbox: InboxCoordinator, client: Any, github: GitHub
) -> None:
    github.issue(2, issue(2))
    payload = event("opened", issue=issue(2))
    assert (await deliver(client, "issues", payload, guid="g1")).status == 200
    assert (await deliver(client, "issues", payload, guid="g1")).status == 200
    await hass.async_block_till_done()
    assert [a["type"] for a in inbox.store.data["activity"]] == ["opened"]
    assert inbox.store.data["deliveries"] == ["g1"]
    assert inbox.store.data["last_delivery"]


async def test_a_delivery_without_the_expected_fields_is_ignored(
    hass: HomeAssistant,
    inbox: InboxCoordinator,
    client: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    resp = await deliver(client, "issues", {"action": "opened"})
    assert resp.status == 200
    assert (
        "Ignored a issues delivery GitHub sent without the expected fields: KeyError"
        in caplog.text
    )
    resp = await deliver(
        client, "ping", {"zen": "Keep it logically awesome."}, guid="g2"
    )
    assert resp.status == 200
    assert inbox.items == {}


# ----------------------------------------------------------- issues, PRs


async def test_an_opened_issue_appears_fires_and_notifies(
    hass: HomeAssistant,
    inbox: InboxCoordinator,
    client: Any,
    github: GitHub,
    phone: list[ServiceCall],
) -> None:
    github.issue(2, issue(2, comments=1, body_html="<p>From GitHub</p>"))
    github.comments(2, [comment(11)])
    # Webhook payloads carry Markdown only.
    await deliver(
        client, "issues", event("opened", issue={**issue(2), "body_html": None})
    )
    assert inbox.items[KEY]["body_html"] == "<p>Hi **there**</p>"
    await hass.async_block_till_done()
    assert inbox.items[KEY]["title"] == "ATX buttons fail with HTTP 500"
    assert hass.states.get(ISSUES).state == "1"
    state = hass.states.get(EVENT)
    assert state.attributes["event_type"] == "opened"
    assert state.attributes["repo"] == REPO
    assert state.attributes["number"] == 2
    assert state.attributes["actor"] == "someone"
    assert not phone

    await later(hass, REFRESH_DELAY + 1)
    assert inbox.items[KEY]["body_html"] == "<p>From GitHub</p>"
    assert [e["id"] for e in inbox.items[KEY]["timeline"]] == ["c11"]

    await later(hass, NOTIFY_DELAY)
    (call,) = phone
    assert call.data["title"] == "someone opened issue ha-glkvm#2"
    assert call.data["data"]["url"] == "/github-inbox?item=heidrickla%2Fha-glkvm%232"
    assert (
        call.data["data"]["actions"][0]["uri"] == f"https://github.com/{REPO}/issues/2"
    )
    assert note(hass)["title"] == "someone opened issue ha-glkvm#2"


async def test_a_burst_is_one_notification(
    hass: HomeAssistant,
    inbox: InboxCoordinator,
    client: Any,
    github: GitHub,
    phone: list[ServiceCall],
) -> None:
    github.issue(2, issue(2))
    github.issue(3, issue(3))
    await deliver(client, "issues", event("opened", issue=issue(2)), guid="a")
    await deliver(client, "issues", event("opened", issue=issue(3)), guid="b")
    await later(hass, NOTIFY_DELAY + 1)
    (call,) = phone
    assert call.data["title"] == "2 updates in your repositories"
    assert "data" in call.data
    assert "actions" not in call.data["data"]


async def test_closed_and_reopened_issues(
    hass: HomeAssistant, inbox: InboxCoordinator, client: Any, github: GitHub
) -> None:
    github.issue(2, issue(2))
    await deliver(client, "issues", event("opened", issue=issue(2)), guid="a")
    inbox.async_mark_seen([KEY])
    await deliver(
        client, "issues", event("closed", issue=issue(2, state="closed")), guid="b"
    )
    assert KEY not in inbox.items
    assert KEY not in inbox.store.data["seen"]
    await deliver(client, "issues", event("reopened", issue=issue(2)), guid="c")
    await deliver(
        client, "issues", event("labeled", issue=issue(2, title="Renamed")), guid="d"
    )
    assert inbox.items[KEY]["title"] == "Renamed"
    assert [a["type"] for a in inbox.store.data["activity"]] == [
        "opened",
        "closed",
        "reopened",
    ]
    await deliver(client, "issues", event("transferred", issue=issue(2)), guid="e")
    assert KEY not in inbox.items


@pytest.mark.parametrize("action", ["labeled", "edited", "locked", "assigned"])
async def test_events_on_a_closed_issue_keep_it_out(
    hass: HomeAssistant, inbox: InboxCoordinator, client: Any, action: str
) -> None:
    await deliver(client, "issues", event(action, issue=issue(2, state="closed")))
    assert KEY not in inbox.items
    assert hass.states.get(ISSUES).state == "0"
    assert inbox.store.data["activity"] == []


async def test_events_on_a_merged_pull_request_keep_it_out(
    hass: HomeAssistant, inbox: InboxCoordinator, client: Any, github: GitHub
) -> None:
    github.pull(5, pull(5))
    await deliver(
        client, "pull_request", event("opened", pull_request=pull(5)), guid="a"
    )
    merged = pull(5, state="closed", merged=True)
    await deliver(
        client, "pull_request", event("labeled", pull_request=merged), guid="b"
    )
    assert PR_KEY not in inbox.items


async def test_pull_requests_open_and_merge(
    hass: HomeAssistant, inbox: InboxCoordinator, client: Any, github: GitHub
) -> None:
    github.pull(5, pull(5), reviews=[review(7, "APPROVED")])
    await deliver(
        client, "pull_request", event("opened", pull_request=pull(5)), guid="a"
    )
    assert inbox.items[PR_KEY]["kind"] == "pr"
    assert inbox.items[PR_KEY]["pr"]["head"] == "fix"
    await later(hass, REFRESH_DELAY + 1)
    assert [e["id"] for e in inbox.items[PR_KEY]["timeline"]] == ["r7"]
    await deliver(
        client,
        "pull_request",
        event("synchronize", pull_request=pull(5, draft=True)),
        guid="b",
    )
    assert inbox.items[PR_KEY]["draft"]
    await deliver(
        client,
        "pull_request",
        event("closed", pull_request=pull(5, state="closed", merged=True)),
        guid="c",
    )
    assert PR_KEY not in inbox.items
    await deliver(
        client,
        "pull_request",
        event("closed", pull_request=pull(6, state="closed")),
        guid="d",
    )
    assert [a["type"] for a in inbox.store.data["activity"]] == [
        "opened",
        "merged",
        "closed",
    ]


# -------------------------------------------------------------- comments


async def test_comments_add_edit_and_delete(
    hass: HomeAssistant, inbox: InboxCoordinator, client: Any, github: GitHub
) -> None:
    payload = event("created", issue=issue(2, comments=1), comment=comment(11))
    await deliver(client, "issue_comment", payload, guid="a")
    assert [e["id"] for e in inbox.items[KEY]["timeline"]] == ["c11"]
    edited = event(
        "edited",
        issue=issue(2),
        comment=comment(11, body_html="<p>Edited</p>", body="Edited"),
    )
    await deliver(client, "issue_comment", edited, guid="b")
    assert inbox.items[KEY]["timeline"][0]["body_html"] == "<p>Edited</p>"
    await deliver(
        client,
        "issue_comment",
        event("deleted", issue=issue(2), comment=comment(11)),
        guid="c",
    )
    assert inbox.items[KEY]["timeline"] == []
    assert [a["type"] for a in inbox.store.data["activity"]] == ["commented"]
    assert inbox.store.data["activity"][0]["url"].endswith("#issuecomment-11")


async def test_a_comment_on_a_closed_issue_is_ignored(
    hass: HomeAssistant, inbox: InboxCoordinator, client: Any
) -> None:
    payload = event("created", issue=issue(2, state="closed"), comment=comment(11))
    await deliver(client, "issue_comment", payload)
    assert inbox.items == {}
    assert inbox.store.data["activity"] == []


async def test_a_comment_on_a_pull_request(
    hass: HomeAssistant, inbox: InboxCoordinator, client: Any, github: GitHub
) -> None:
    github.pull(5, pull(5), comments=[comment(11)])
    payload = event("created", issue=issue(5, pull_request=True), comment=comment(11))
    await deliver(client, "issue_comment", payload)
    assert inbox.items[PR_KEY]["kind"] == "pr"
    await later(hass, REFRESH_DELAY + 1)
    assert github.calls("get", f"/repos/{REPO}/pulls/5") == 1


async def test_reviews_and_line_comments(
    hass: HomeAssistant, inbox: InboxCoordinator, client: Any
) -> None:
    await deliver(
        client, "pull_request", event("opened", pull_request=pull(5)), guid="0"
    )
    stats = inbox.items[PR_KEY]["pr"]
    approved = event(
        "submitted", pull_request=short_pull(5), review=review(7, "APPROVED")
    )
    await deliver(client, "pull_request_review", approved, guid="a")
    empty = event(
        "submitted", pull_request=short_pull(5), review=review(8, "COMMENTED", body="")
    )
    await deliver(client, "pull_request_review", empty, guid="b")
    line = event(
        "created",
        pull_request=short_pull(5),
        comment=review_comment(30, at="2026-10-21T11:05:00Z"),
    )
    await deliver(client, "pull_request_review_comment", line, guid="c")
    # The short form carries no counts; the stored ones stay.
    assert inbox.items[PR_KEY]["pr"] == stats
    assert inbox.items[PR_KEY]["comments"] == 3
    timeline = inbox.items[PR_KEY]["timeline"]
    assert [e["id"] for e in timeline] == ["r7", "d30"]
    assert timeline[0]["state"] == "approved"
    assert timeline[1]["path"] == "custom_components/glkvm/api.py"
    gone = event("deleted", pull_request=short_pull(5), comment=review_comment(30))
    await deliver(client, "pull_request_review_comment", gone, guid="d")
    assert [e["id"] for e in inbox.items[PR_KEY]["timeline"]] == ["r7"]
    closed = event(
        "submitted", pull_request=pull(5, state="closed"), review=review(9, "APPROVED")
    )
    await deliver(client, "pull_request_review", closed, guid="e")
    line_closed = event(
        "created", pull_request=pull(5, state="closed"), comment=review_comment(31)
    )
    await deliver(client, "pull_request_review_comment", line_closed, guid="f")
    types = [(a["type"], a["state"]) for a in inbox.store.data["activity"]]
    assert types == [
        ("opened", None),
        ("reviewed", "approved"),
        ("reviewed", "commented"),
    ]


# ---------------------------------------------------------- notify rules


async def test_own_and_bot_activity_is_recorded_and_not_notified(
    hass: HomeAssistant,
    inbox: InboxCoordinator,
    client: Any,
    github: GitHub,
    phone: list[ServiceCall],
) -> None:
    github.issue(2, issue(2))
    await deliver(
        client,
        "issues",
        event("opened", sender=ACCOUNT, issue=issue(2, author=ACCOUNT)),
        guid="a",
    )
    bot = event("created", issue=issue(2), comment=comment(11, author="renovate[bot]"))
    await deliver(client, "issue_comment", bot, guid="b")
    await later(hass, NOTIFY_DELAY + 1)
    assert not phone
    activity = inbox.store.data["activity"]
    assert [(a["own"], a["bot"]) for a in activity] == [(True, False), (False, True)]


async def test_an_organization_inbox_knows_you_by_your_logins(
    hass: HomeAssistant,
    pem: str,
    github: GitHub,
    hass_client_no_auth: Any,
    phone: list[ServiceCall],
) -> None:
    github.token()
    github.repos([REPO])
    github.issues([])
    github.issue(2, issue(2))
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="my-org",
        unique_id=str(ACCOUNT_ID),
        data=entry_data(pem, account="my-org"),
        options={
            **OPTIONS,
            CONF_ME: ["heidrickla"],
            CONF_NOTIFY_TARGETS: ["mobile_app_phone"],
        },
    )
    inbox = await setup_entry(hass, entry)
    client = await hass_client_no_auth()
    await deliver(client, "issues", event("opened", issue=issue(2)), guid="a")
    inbox.async_mark_seen([KEY])
    mine = event(
        "created",
        sender="HeidrickLA",
        issue=issue(2),
        comment=comment(11, author="HeidrickLA"),
    )
    await deliver(client, "issue_comment", mine, guid="b")
    await later(hass, NOTIFY_DELAY + 1)
    assert [a["own"] for a in inbox.store.data["activity"]] == [False, True]
    assert len(phone) == 1
    assert inbox.data["unseen"] == 0
    item = inbox.account_view()
    assert item["me"] == {"heidrickla"}


async def test_notify_options_choose_what_is_sent(
    hass: HomeAssistant,
    inbox: InboxCoordinator,
    client: Any,
    github: GitHub,
    phone: list[ServiceCall],
) -> None:
    entry = inbox.config_entry
    hass.config_entries.async_update_entry(
        entry,
        options={
            **entry.options,
            CONF_NOTIFY_ON: [NOTIFY_CLOSED],
            CONF_INCLUDE_OWN: True,
            CONF_INCLUDE_BOTS: True,
        },
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    inbox = entry.runtime_data
    github.issue(2, issue(2))
    await deliver(client, "issues", event("opened", issue=issue(2)), guid="a")
    await deliver(
        client,
        "issues",
        event("closed", sender=ACCOUNT, issue=issue(2, state="closed")),
        guid="b",
    )
    await later(hass, NOTIFY_DELAY + 1)
    (call,) = phone
    assert call.data["title"] == f"{ACCOUNT} closed issue ha-glkvm#2"


async def test_a_failing_notifier_is_logged_once(
    hass: HomeAssistant,
    inbox: InboxCoordinator,
    client: Any,
    github: GitHub,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def broken(call: ServiceCall) -> None:
        raise HomeAssistantError("phone gone")

    hass.services.async_register("notify", "mobile_app_phone", broken)
    github.issue(2, issue(2))
    github.issue(3, issue(3))
    await deliver(client, "issues", event("opened", issue=issue(2)), guid="a")
    await later(hass, NOTIFY_DELAY + 1)
    await deliver(client, "issues", event("opened", issue=issue(3)), guid="b")
    await later(hass, NOTIFY_DELAY + 1)
    assert caplog.text.count("Could not notify through notify.mobile_app_phone") == 1
    assert note(hass)["title"] == "someone opened issue ha-glkvm#3"


async def test_a_notifier_that_hangs_is_given_up_on(
    hass: HomeAssistant,
    inbox: InboxCoordinator,
    client: Any,
    github: GitHub,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def hangs(call: ServiceCall) -> None:
        await asyncio.Event().wait()

    hass.services.async_register("notify", "mobile_app_phone", hangs)
    github.issue(2, issue(2))
    with patch.object(coordinator_module, "NOTIFY_TIMEOUT", 0.05):
        await deliver(client, "issues", event("opened", issue=issue(2)), guid="a")
        await later(hass, NOTIFY_DELAY + 1)
    assert "Could not notify through notify.mobile_app_phone" in caplog.text
    assert note(hass)["title"] == "someone opened issue ha-glkvm#2"


async def test_each_item_or_batch_keeps_its_own_card(
    hass: HomeAssistant,
    inbox: InboxCoordinator,
    client: Any,
    github: GitHub,
    phone: list[ServiceCall],
) -> None:
    github.issue(2, issue(2))
    github.issue(3, issue(3))
    await deliver(client, "issues", event("opened", issue=issue(2)), guid="a")
    await later(hass, NOTIFY_DELAY + 1)
    await deliver(client, "issues", event("opened", issue=issue(3)), guid="b")
    await later(hass, NOTIFY_DELAY + 1)
    cards = persistent_notification._async_get_or_create_notifications(hass)
    assert sorted(c["title"] for c in cards.values()) == [
        "someone opened issue ha-glkvm#2",
        "someone opened issue ha-glkvm#3",
    ]


async def test_the_activity_log_and_delivery_ids_are_bounded(
    hass: HomeAssistant, inbox: InboxCoordinator
) -> None:
    old = (dt_util.utcnow() - dt.timedelta(days=ACTIVITY_DAYS + 1)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    inbox.store.data["activity"] = [{"at": old, "type": "opened"}]
    item = {
        "key": KEY,
        "repo": REPO,
        "number": 2,
        "kind": "issue",
        "title": "t",
        "url": "",
    }
    for n in range(ACTIVITY_KEEP + 5):
        inbox._record(inbox._entry("commented", item, user(f"u{n}")))
    log = inbox.store.data["activity"]
    assert len(log) == ACTIVITY_KEEP
    assert all(a["at"] != old for a in log)
    assert log[-1]["actor"] == f"u{ACTIVITY_KEEP + 4}"
    for n in range(DELIVERY_KEEP + 3):
        inbox.async_receive("ping", f"d{n}", {})
    deliveries = inbox.store.data["deliveries"]
    assert len(deliveries) == DELIVERY_KEEP
    assert deliveries[0] == "d3"
    inbox.async_receive("ping", "d0", {})
    assert deliveries[-1] == "d0"


async def test_no_targets_means_no_notification(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    github: GitHub,
    hass_client_no_auth: Any,
) -> None:
    github.token()
    github.repos([REPO])
    github.issues([])
    github.issue(2, issue(2))
    inbox = await setup_entry(hass, config_entry)
    client = await hass_client_no_auth()
    await deliver(client, "issues", event("opened", issue=issue(2)))
    await later(hass, NOTIFY_DELAY + 1)
    assert note(hass) is None
    assert inbox.items[KEY]


# --------------------------------------------------------------- refresh


async def test_a_refresh_that_finds_the_item_closed_removes_it(
    hass: HomeAssistant, inbox: InboxCoordinator, client: Any, github: GitHub
) -> None:
    github.issue(2, issue(2, state="closed"))
    await deliver(client, "issues", event("opened", issue=issue(2)))
    await later(hass, REFRESH_DELAY + 1)
    assert KEY not in inbox.items


async def test_a_refresh_that_finds_the_item_gone_removes_it(
    hass: HomeAssistant, inbox: InboxCoordinator, client: Any, github: GitHub
) -> None:
    github.issue(2, {}, status=404)
    await deliver(client, "issues", event("opened", issue=issue(2)))
    await later(hass, REFRESH_DELAY + 1)
    assert KEY not in inbox.items


async def test_a_refresh_that_fails_keeps_the_webhook_copy(
    hass: HomeAssistant, inbox: InboxCoordinator, client: Any, github: GitHub
) -> None:
    github.issue(2, {}, status=502)
    await deliver(client, "issues", event("opened", issue=issue(2)))
    await later(hass, REFRESH_DELAY + 1)
    assert inbox.items[KEY]["title"] == "ATX buttons fail with HTTP 500"


async def test_a_refresh_failure_is_logged_once_and_its_recovery_once(
    hass: HomeAssistant,
    inbox: InboxCoordinator,
    client: Any,
    github: GitHub,
    caplog: pytest.LogCaptureFixture,
) -> None:
    github.issue(2, {}, status=502)
    github.issue(3, {}, status=502)
    await deliver(client, "issues", event("opened", issue=issue(2)), guid="a")
    await deliver(client, "issues", event("opened", issue=issue(3)), guid="b")
    await later(hass, REFRESH_DELAY + 1)
    assert caplog.text.count("Kept the webhook copy") == 1
    github.reset()
    github.token()
    github.issue(2, issue(2))
    github.comments(2, [])
    await deliver(client, "issues", event("edited", issue=issue(2)), guid="c")
    await later(hass, REFRESH_DELAY + 1)
    assert caplog.text.count("Reading items from GitHub works again") == 1


async def test_a_refresh_github_refuses_for_auth_leads_to_reauth(
    hass: HomeAssistant, inbox: InboxCoordinator, client: Any, github: GitHub
) -> None:
    await deliver(client, "issues", event("opened", issue=issue(2)), guid="a")
    github.reset()
    github.token(status=401)
    github.repos([REPO])
    inbox.client._token = None
    await later(hass, REFRESH_DELAY + 1)
    await later(hass, REFRESH_DELAY + 1)
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["source"] for f in flows] == [SOURCE_REAUTH]
    assert inbox.items[KEY]


async def test_an_event_during_a_refresh_wins(
    hass: HomeAssistant, inbox: InboxCoordinator, client: Any, github: GitHub
) -> None:
    github.issue(2, issue(2, title="Stale copy"))
    await deliver(client, "issues", event("opened", issue=issue(2)), guid="a")
    real = inbox.client.issue

    async def slow(repo: str, number: int) -> dict[str, Any]:
        inbox.async_receive(
            "issues", "b", event("edited", issue=issue(2, title="Newest"))
        )
        return await real(repo, number)

    with patch.object(inbox.client, "issue", slow):
        await later(hass, REFRESH_DELAY + 1)
    assert inbox.items[KEY]["title"] == "Newest"


async def test_a_second_event_restarts_the_refresh_timer(
    hass: HomeAssistant, inbox: InboxCoordinator, client: Any, github: GitHub
) -> None:
    github.issue(2, issue(2))
    await deliver(client, "issues", event("opened", issue=issue(2)), guid="a")
    await later(hass, REFRESH_DELAY - 1)
    await deliver(client, "issues", event("edited", issue=issue(2)), guid="b")
    await later(hass, REFRESH_DELAY + 1)
    assert github.calls("get", f"/repos/{REPO}/issues/2") == 1


# ---------------------------------------------------- installation, repos


async def test_an_uninstalled_app_makes_the_inbox_unavailable_and_asks_for_reauth(
    hass: HomeAssistant,
    inbox: InboxCoordinator,
    client: Any,
    github: GitHub,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await deliver(client, "installation", installation("deleted", 999), guid="z")
    assert not inbox.installation_lost
    await deliver(client, "installation", installation("deleted"), guid="a")
    await deliver(client, "installation", installation("suspend"), guid="a2")
    assert inbox.installation_lost
    assert (
        caplog.text.count("the inbox is unavailable until it is installed again") == 1
    )
    assert hass.states.get(ISSUES).state == STATE_UNAVAILABLE
    assert inbox.account_view()["sync"]["state"] == "lost"
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["source"] for f in flows] == [SOURCE_REAUTH]
    before = github.calls("get", "/installation/repositories")
    await deliver(
        client, "installation", installation("unsuspend", account=99), guid="y"
    )
    assert inbox.installation_lost
    await deliver(client, "installation", installation("unsuspend"), guid="b")
    assert not inbox.installation_lost
    assert caplog.text.count("reports the app installed again") == 1
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    await later(hass, REFRESH_DELAY + 1)
    assert github.calls("get", "/installation/repositories") == before + 1
    assert hass.states.get(ISSUES).state == "0"


async def test_a_reinstalled_app_moves_the_entry_to_the_new_installation(
    hass: HomeAssistant,
    inbox: InboxCoordinator,
    client: Any,
    github: GitHub,
    aioclient_mock: Any,
) -> None:
    await deliver(client, "installation", installation("deleted"), guid="a")
    aioclient_mock.post(
        f"https://api.github.com/app/installations/{INSTALLATION_ID + 1}/access_tokens",
        status=201,
        json={"token": "ghs_new", "expires_at": "2099-01-01T00:00:00Z"},
    )
    await deliver(
        client, "installation", installation("created", INSTALLATION_ID + 1), guid="b"
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    entry = inbox.config_entry
    assert entry.data["installation_id"] == INSTALLATION_ID + 1
    assert entry.runtime_data is not inbox
    assert not entry.runtime_data.installation_lost
    assert hass.states.get(ISSUES).state == "0"
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)


async def test_repositories_removed_from_the_installation_drop_their_items(
    hass: HomeAssistant, inbox: InboxCoordinator, client: Any, github: GitHub
) -> None:
    github.issue(2, issue(2))
    await deliver(client, "issues", event("opened", issue=issue(2)), guid="a")
    payload = {
        "action": "removed",
        "repositories_removed": [repo_obj()],
        "repositories_added": [],
    }
    await deliver(client, "installation_repositories", payload, guid="b")
    assert inbox.items == {}
    assert inbox.store.data["repos"] == []
    before = github.calls("get", "/installation/repositories")
    payload = {"action": "added", "repositories_added": [repo_obj("heidrickla/new")]}
    await deliver(client, "installation_repositories", payload, guid="c")
    await later(hass, REFRESH_DELAY + 1)
    assert github.calls("get", "/installation/repositories") == before + 1


@pytest.mark.parametrize("action", ["deleted", "archived"])
async def test_a_deleted_or_archived_repository_drops_its_items(
    hass: HomeAssistant,
    inbox: InboxCoordinator,
    client: Any,
    github: GitHub,
    action: str,
) -> None:
    github.issue(2, issue(2))
    await deliver(client, "issues", event("opened", issue=issue(2)), guid="a")
    await deliver(client, "repository", event(action), guid="b")
    assert inbox.items == {}


async def test_a_transferred_repository_drops_the_old_name(
    hass: HomeAssistant, inbox: InboxCoordinator, client: Any, github: GitHub
) -> None:
    github.issue(2, issue(2))
    await deliver(client, "issues", event("opened", issue=issue(2)), guid="a")
    payload = {
        "action": "transferred",
        "repository": {"full_name": "neworg/ha-glkvm", "name": "ha-glkvm"},
        "changes": {"owner": {"from": {"user": {"login": "heidrickla"}}}},
    }
    await deliver(client, "repository", payload, guid="b")
    assert inbox.items == {}


async def test_a_renamed_repository_moves_its_items_and_resyncs(
    hass: HomeAssistant, inbox: InboxCoordinator, client: Any, github: GitHub
) -> None:
    await deliver(client, "issues", event("opened", issue=issue(2)), guid="a")
    inbox.async_mark_seen([KEY])
    seen_at = inbox.store.data["seen"][KEY]
    before = github.calls("get", "/installation/repositories")
    new = "heidrickla/ha-glkvm2"
    payload = {
        "action": "renamed",
        "repository": {"full_name": new, "name": "ha-glkvm2"},
        "changes": {"repository": {"name": {"from": "ha-glkvm"}}},
    }
    await deliver(client, "repository", payload, guid="b")
    assert set(inbox.items) == {f"{new}#2"}
    assert inbox.items[f"{new}#2"]["repo"] == new
    assert inbox.store.data["seen"] == {f"{new}#2": seen_at}
    assert [a["key"] for a in inbox.store.data["activity"]] == [f"{new}#2"]
    assert inbox.store.data["repos"] == [new]
    assert inbox.data["unseen"] == 0
    await later(hass, REFRESH_DELAY + 1)
    assert github.calls("get", "/installation/repositories") == before + 1


async def test_activity_listeners_and_event_entity(
    hass: HomeAssistant, inbox: InboxCoordinator, client: Any, github: GitHub
) -> None:
    seen: list[dict[str, Any]] = []
    remove = inbox.async_add_activity_listener(seen.append)
    changes = async_capture_events(hass, "state_changed")
    github.issue(2, issue(2))
    await deliver(client, "issues", event("opened", issue=issue(2)), guid="a")
    remove()
    await deliver(
        client, "issues", event("closed", issue=issue(2, state="closed")), guid="b"
    )
    await hass.async_block_till_done()
    assert [e["type"] for e in seen] == ["opened"]
    assert [
        c.data["new_state"].attributes.get("event_type")
        for c in changes
        if c.data["entity_id"] == EVENT
    ] == [
        "opened",
        "closed",
    ]
