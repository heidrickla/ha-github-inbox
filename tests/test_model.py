"""Items from GitHub's JSON, the unseen and waiting rules, and the panel snapshot."""

from __future__ import annotations

import datetime as dt

from custom_components.github_inbox import model
from tests.payloads import comment, issue, pull, review, review_comment, short_pull

NOW = dt.datetime(2026, 10, 23, 12, 0, tzinfo=dt.UTC)
REPO = "heidrickla/ha-glkvm"
ME = model.logins(["heidrickla"])


def test_issue_item_takes_github_rendering_and_signature() -> None:
    item = model.item_from_issue(REPO, issue(2, body_html="<p>Hi <b>there</b></p>"))
    assert item["key"] == f"{REPO}#2"
    assert item["kind"] == "issue"
    assert item["body_html"] == "<p>Hi <b>there</b></p>"
    assert item["preview"] == "Hi there"
    assert item["body_sig"] == model.signature("Hi **there**")
    assert item["labels"] == [{"name": "bug", "color": "d73a4a"}]


def test_a_listed_pull_request_is_a_pr_and_bad_label_colours_fall_back() -> None:
    raw = issue(3, pull_request=True)
    raw["labels"] = [{"name": "x", "color": "zzzzzz"}, {"color": "ffffff"}, "junk"]
    item = model.item_from_issue(REPO, raw)
    assert item["kind"] == "pr"
    assert item["labels"] == [{"name": "x", "color": model.LABEL_DEFAULT}]


def test_webhook_copy_without_rendering_is_escaped_text() -> None:
    raw = issue(2)
    raw.pop("body_html")
    raw["body"] = "a <b>"
    assert model.item_from_issue(REPO, raw)["body_html"] == "<p>a &lt;b&gt;</p>"


def test_pull_item_carries_diff_stats_and_review_comment_count() -> None:
    item = model.item_from_pull(REPO, pull(5, draft=True))
    assert item["kind"] == "pr"
    assert item["draft"] is True
    assert item["comments"] == 3
    assert item["pr"] == {
        "additions": 12,
        "deletions": 3,
        "changed_files": 2,
        "head": "fix",
        "base": "main",
    }


def test_merge_keeps_rendering_timeline_and_pr_details_when_the_source_is_unchanged() -> (
    None
):
    stored = model.merge(None, model.item_from_pull(REPO, pull(5)))
    stored["timeline"] = [model.comment_entry(comment(1))]
    assert stored["rev"] == 1
    plain = model.item_from_issue(
        REPO, {**issue(5, pull_request=True, body="Fixes #2"), "body_html": ""}
    )
    merged = model.merge(stored, plain)
    assert merged["body_html"] == stored["body_html"]
    assert merged["pr"] == stored["pr"]
    assert merged["timeline"] == stored["timeline"]
    assert merged["rev"] == 2
    edited = model.merge(
        stored, {**plain, "body_sig": "other", "body_html": "<p>new</p>"}
    )
    assert edited["body_html"] == "<p>new</p>"


def test_github_rendering_replaces_a_webhook_plain_copy_of_the_same_source() -> None:
    plain = model.item_from_issue(REPO, {**issue(2), "body_html": ""})
    assert not plain["body_rendered"]
    stored = model.merge(None, plain)
    merged = model.merge(stored, model.item_from_issue(REPO, issue(2)))
    assert merged["body_html"] == "<p>Hi <strong>there</strong></p>"
    assert merged["body_rendered"]
    item = model.merge(None, model.item_from_issue(REPO, issue(2)))
    model.upsert_entry(item, model.comment_entry({**comment(1), "body_html": ""}))
    model.upsert_entry(item, model.comment_entry(comment(1)))
    assert item["timeline"][0]["body_html"] == "<p>Thanks</p>"
    assert item["timeline"][0]["body_rendered"]


def test_a_review_webhooks_short_pull_request_keeps_the_stored_counts() -> None:
    stored = model.merge(None, model.item_from_pull(REPO, pull(5)))
    partial = model.item_from_pull(REPO, short_pull(5))
    assert partial["pr"] is None
    assert "comments" not in partial
    merged = model.merge(stored, partial)
    assert merged["pr"] == stored["pr"]
    assert merged["comments"] == stored["comments"] == 3
    assert model.merge(None, partial)["comments"] == 0


def test_timeline_orders_entries_and_skips_container_reviews() -> None:
    entries = model.timeline(
        [comment(1, at="2026-10-20T10:00:00Z")],
        [
            review(7, "APPROVED", at="2026-10-21T10:00:00Z"),
            review(8, "COMMENTED", body="", at="2026-10-21T11:00:00Z"),
            review(9, "PENDING", at="2026-10-21T12:00:00Z"),
            review(10, "COMMENTED", body="looks fine", at="2026-10-19T10:00:00Z"),
        ],
        [review_comment(4, at="2026-10-22T10:00:00Z")],
    )
    assert [e["id"] for e in entries] == ["r10", "c1", "r7", "d4"]
    assert entries[2]["state"] == "approved"
    assert entries[3]["path"] == "custom_components/glkvm/api.py"
    assert entries[3]["line"] == 42


def test_timeline_keeps_the_newest_entries() -> None:
    many = [
        comment(n, at=f"2026-10-{1 + n // 24:02d}T{n % 24:02d}:00:00Z")
        for n in range(model.TIMELINE_KEEP + 5)
    ]
    entries = model.timeline(many)
    assert len(entries) == model.TIMELINE_KEEP
    assert entries[0]["id"] == "c5"


def test_upsert_entry_replaces_by_id_and_keeps_rendering_for_the_same_source() -> None:
    item = model.merge(None, model.item_from_issue(REPO, issue(2)))
    first = model.comment_entry(comment(1, body_html="<p>rendered</p>"))
    model.upsert_entry(item, first)
    plain = model.comment_entry({**comment(1), "body_html": ""})
    model.upsert_entry(item, plain)
    assert item["timeline"][0]["body_html"] == "<p>rendered</p>"
    model.upsert_entry(item, model.comment_entry(comment(1, body="changed")))
    assert item["timeline"][0]["body_html"] != "<p>rendered</p>"
    model.upsert_entry(item, model.comment_entry(comment(0, at="2026-10-01T00:00:00Z")))
    assert [e["id"] for e in item["timeline"]] == ["c0", "c1"]
    model.remove_entry(item, "c0")
    assert [e["id"] for e in item["timeline"]] == ["c1"]


def test_activity_and_unseen_ignore_you() -> None:
    item = model.merge(None, model.item_from_issue(REPO, issue(2, author="HeidrickLA")))
    assert model.activity_at(item, ME) == ""
    assert not model.unseen(item, {}, ME)
    model.upsert_entry(
        item,
        model.comment_entry(comment(1, author="someone", at="2026-10-22T10:00:00Z")),
    )
    assert model.unseen(item, {}, ME)
    assert not model.unseen(item, {item["key"]: "2026-10-22T10:00:00Z"}, ME)
    assert model.unseen(item, {item["key"]: "2026-10-22T09:59:59Z"}, ME)


def test_logins_are_compared_without_case_or_blanks() -> None:
    assert model.logins(["HeidrickLA", " ", "", "Other "]) == {"heidrickla", "other"}
    assert model.is_me("HEIDRICKLA", ME)
    assert not model.is_me("", ME)


def test_last_word_and_waiting() -> None:
    item = model.merge(None, model.item_from_issue(REPO, issue(2, author="someone")))
    assert model.last_word(item) == ("someone", item["created_at"])
    assert model.summary(item, False, "my-org", ME)["waiting"] is True
    model.upsert_entry(item, model.comment_entry(comment(1, author="heidrickla")))
    summary = model.summary(item, False, "my-org", ME)
    assert summary["waiting"] is False
    assert summary["account"] == "my-org"
    assert summary["me"] == ["heidrickla"]
    assert summary["last"]["author"] == "heidrickla"
    assert summary["comments"] == 1


def _account(items: list[model.Item], activity: list[dict] | None = None) -> dict:
    return {
        "account": "heidrickla",
        "me": ME,
        "items": {i["key"]: i for i in items},
        "seen": {},
        "activity": activity or [],
        "repos": 40,
        "sync": {"state": "ok"},
    }


def test_snapshot_counts_groups_ages_and_days() -> None:
    a = model.merge(
        None,
        model.item_from_issue(
            REPO,
            issue(1, created="2026-10-23T06:00:00Z", updated="2026-10-23T07:00:00Z"),
        ),
    )
    b = model.merge(
        None,
        model.item_from_pull(
            REPO,
            pull(2, created="2026-08-01T00:00:00Z", updated="2026-10-22T00:00:00Z"),
        ),
    )
    c = model.merge(
        None,
        model.item_from_pull(
            "heidrickla/other",
            pull(
                3,
                draft=True,
                created="2024-01-01T00:00:00Z",
                updated="2026-01-01T00:00:00Z",
            ),
        ),
    )
    activity = [
        {"at": "2026-10-23T08:00:00Z", "type": "opened"},
        {"at": "2026-10-23T09:00:00Z", "type": "merged"},
        {"at": "2026-10-22T09:00:00Z", "type": "commented"},
        {"at": "2026-10-01T09:00:00Z", "type": "commented"},
        {"at": "bad", "type": "commented"},
        {"at": "2026-10-23T09:00:00Z", "type": "unknown"},
    ]
    snap = model.snapshot([_account([a, b, c], activity)], NOW)
    assert [i["key"] for i in snap["items"]] == [a["key"], b["key"], c["key"]]
    t = snap["totals"]
    assert (t["issues"], t["prs"], t["drafts"], t["repos"], t["repos_installed"]) == (
        1,
        2,
        1,
        2,
        40,
    )
    assert t["unseen"] == 3
    assert t["waiting"] == 3
    assert snap["by_repo"][0] == {
        "repo": REPO,
        "issues": 1,
        "prs": 1,
        "drafts": 0,
        "unseen": 2,
    }
    assert snap["by_repo"][1]["drafts"] == 1
    assert [x["issues"] + x["prs"] for x in snap["ages"]] == [1, 0, 0, 1, 0, 1]
    assert len(snap["days"]) == model.DAYS
    assert snap["days"][-1] == {
        "date": "2026-10-23",
        "opened": 1,
        "commented": 0,
        "reviewed": 0,
        "closed": 1,
    }
    assert snap["days"][-2]["commented"] == 1
    assert t["activity_7d"] == 3
    assert len(snap["recent"]) == 5
    assert snap["recent"][0]["at"] == "2026-10-23T09:00:00Z"
    assert snap["oldest"] == {"key": c["key"], "created_at": "2024-01-01T00:00:00Z"}
    assert snap["sync"] == [{"account": "heidrickla", "state": "ok"}]


def test_snapshot_of_nothing() -> None:
    snap = model.snapshot([], NOW)
    assert snap["items"] == []
    assert snap["oldest"] is None
    assert snap["totals"]["issues"] == 0


def test_parse_and_iso() -> None:
    assert model.parse(None) is None
    assert model.parse("nope") is None
    assert model.parse("2026-10-23T01:02:03") == dt.datetime(
        2026, 10, 23, 1, 2, 3, tzinfo=dt.UTC
    )
    assert (
        model.iso(
            dt.datetime(
                2026, 10, 23, 7, 2, 3, tzinfo=dt.timezone(dt.timedelta(hours=-5))
            )
        )
        == "2026-10-23T12:02:03Z"
    )


def test_bots_and_logins() -> None:
    assert model.is_bot({"login": "dependabot[bot]", "type": "Bot"})
    assert model.is_bot({"login": "renovate[bot]"})
    assert not model.is_bot({"login": "someone", "type": "User"})
    assert not model.is_bot(None)
    assert model.login(None) == ""
