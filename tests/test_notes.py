"""Notification text and the notify call's data."""

from __future__ import annotations

from custom_components.github_inbox import notes


def entry(type_: str = "opened", **extra):
    return {
        "type": type_,
        "key": "heidrickla/ha-glkvm#2",
        "repo": "heidrickla/ha-glkvm",
        "number": 2,
        "kind": "issue",
        "title": "ATX buttons fail",
        "url": "https://github.com/heidrickla/ha-glkvm/issues/2",
        "actor": "someone",
        "preview": "",
        **extra,
    }


def test_describe_each_kind() -> None:
    assert (
        notes.describe(entry()) == "someone opened issue ha-glkvm#2: ATX buttons fail"
    )
    assert (
        notes.describe(entry("reopened", kind="pr"))
        == "someone reopened pull request ha-glkvm#2: ATX buttons fail"
    )
    assert notes.describe(entry("commented")).startswith(
        "someone commented on ha-glkvm#2"
    )
    assert notes.describe(entry("reviewed", state="approved")).startswith(
        "someone approved ha-glkvm#2"
    )
    assert notes.describe(entry("reviewed", state="changes_requested")).startswith(
        "someone requested changes on"
    )
    assert notes.describe(entry("reviewed", state="commented")).startswith(
        "someone reviewed"
    )
    assert notes.describe(entry("merged")).startswith("someone merged ha-glkvm#2")
    assert notes.describe(entry("closed", actor="")).startswith("someone closed issue")


def test_one_entry_links_to_its_item() -> None:
    note = notes.compose([entry("commented", preview="Same here")])
    assert note["title"] == "someone commented on ha-glkvm#2"
    assert note["message"] == "ATX buttons fail\nSame here"
    assert note["link"] == "/github-inbox?item=heidrickla%2Fha-glkvm%232"
    assert note["markdown"].startswith(
        "[ha\\-glkvm\\#2 ATX buttons fail](https://github.com/"
    )
    assert notes.compose([entry()])["message"] == "ATX buttons fail"


def test_markdown_carries_titles_and_previews_as_text() -> None:
    hostile = "x](https://attacker.example) ![t](https://attacker.example/p.png)"
    note = notes.compose(
        [entry(title=hostile, preview="![p](https://attacker.example/q)")]
    )
    md = note["markdown"]
    assert "](https://attacker.example" not in md
    assert md.count("](") == 1
    assert "\\!\\[p\\]\\(https://attacker\\.example/q\\)" in md
    batch = notes.compose([entry(title=hostile), entry()])["markdown"]
    assert "](https://attacker.example" not in batch
    assert batch.count("](https://github.com/") == 2


def test_only_github_addresses_become_links() -> None:
    assert (
        "](" not in notes.compose([entry(url="https://attacker.example/x")])["markdown"]
    )
    assert (
        "](" not in notes.compose([entry(url="https://github.com/a) (b")])["markdown"]
    )
    assert "](" not in notes.compose([entry(url=None)])["markdown"]


def test_a_batch_is_one_message_capped_at_six_lines() -> None:
    note = notes.compose([entry() for _ in range(8)], missed=True)
    assert (
        note["title"]
        == "8 updates in your repositories while Home Assistant was not listening"
    )
    lines = note["message"].split("\n")
    assert len(lines) == 7
    assert lines[-1] == "and 2 more"
    assert note["link"] == "/github-inbox"
    assert note["url"] == ""
    assert note["markdown"].endswith("- and 2 more")


def test_only_the_companion_app_gets_link_data() -> None:
    note = notes.compose([entry()])
    assert notes.service_data("telegram", note) == {
        "title": note["title"],
        "message": note["message"],
    }
    data = notes.service_data("mobile_app_pixel", note)["data"]
    assert data["clickAction"] == data["url"] == note["link"]
    assert data["actions"][0]["uri"] == note["url"]
    batch = notes.compose([entry(), entry()])
    assert "actions" not in notes.service_data("mobile_app_pixel", batch)["data"]
