"""Notification text for a batch of activity entries. Plain functions; the coordinator sends them."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import quote

from .const import (
    ACTIVITY_CLOSED,
    ACTIVITY_COMMENTED,
    ACTIVITY_MERGED,
    ACTIVITY_OPENED,
    ACTIVITY_REOPENED,
    ACTIVITY_REVIEWED,
    NOTIFY_CLOSED,
    NOTIFY_COMMENTS,
    NOTIFY_OPENED,
    NOTIFY_REVIEWS,
    PANEL_URL,
)

GROUP = {
    ACTIVITY_OPENED: NOTIFY_OPENED,
    ACTIVITY_REOPENED: NOTIFY_OPENED,
    ACTIVITY_COMMENTED: NOTIFY_COMMENTS,
    ACTIVITY_REVIEWED: NOTIFY_REVIEWS,
    ACTIVITY_CLOSED: NOTIFY_CLOSED,
    ACTIVITY_MERGED: NOTIFY_CLOSED,
}
REVIEW_WORDS = {
    "approved": "approved",
    "changes_requested": "requested changes on",
    "dismissed": "dismissed a review on",
}
LIST_LINES = 6


def _noun(entry: Mapping[str, Any]) -> str:
    return "pull request" if entry.get("kind") == "pr" else "issue"


def _ref(entry: Mapping[str, Any]) -> str:
    return f"{str(entry['repo']).split('/')[-1]}#{entry['number']}"


def describe(entry: Mapping[str, Any]) -> str:
    """One line: who did what to which item."""
    actor = entry.get("actor") or "someone"
    kind = entry.get("type")
    if kind in (ACTIVITY_OPENED, ACTIVITY_REOPENED):
        verb = f"{kind} {_noun(entry)}"
    elif kind == ACTIVITY_COMMENTED:
        verb = "commented on"
    elif kind == ACTIVITY_REVIEWED:
        verb = REVIEW_WORDS.get(str(entry.get("state") or ""), "reviewed")
    elif kind == ACTIVITY_MERGED:
        verb = "merged"
    else:
        verb = f"closed {_noun(entry)}"
    return f"{actor} {verb} {_ref(entry)}: {entry.get('title', '')}"


def panel_link(key: str | None = None) -> str:
    return f"/{PANEL_URL}" + (f"?item={quote(key, safe='')}" if key else "")


def compose(notes: Sequence[Mapping[str, Any]], missed: bool = False) -> dict[str, Any]:
    """Title, message, a Markdown message and the deep link for one notification."""
    if len(notes) == 1:
        entry = notes[0]
        title = describe(entry).split(": ", 1)[0]
        body = str(entry.get("title", ""))
        snippet = str(entry.get("preview") or "")
        message = f"{body}\n{snippet}" if snippet else body
        markdown = f"[{_ref(entry)} {body}]({entry.get('url', '')})" + (
            f"\n\n{snippet}" if snippet else ""
        )
        return {
            "title": title,
            "message": message,
            "markdown": markdown,
            "link": panel_link(str(entry["key"])),
            "url": str(entry.get("url") or ""),
        }
    lines = [describe(e) for e in notes[:LIST_LINES]]
    more = len(notes) - LIST_LINES
    if more > 0:
        lines.append(f"and {more} more")
    when = " while Home Assistant was not listening" if missed else ""
    md_lines = [
        f"- [{describe(e)}]({e.get('url', '')})" for e in notes[:LIST_LINES]
    ] + ([f"- and {more} more"] if more > 0 else [])
    return {
        "title": f"{len(notes)} updates in your repositories{when}",
        "message": "\n".join(lines),
        "markdown": "\n".join(md_lines),
        "link": panel_link(),
        "url": "",
    }


def service_data(target: str, note: Mapping[str, Any]) -> dict[str, Any]:
    """The notify call's data. Only the companion app gets its link and action keys; other notifiers can reject them."""
    data: dict[str, Any] = {"title": note["title"], "message": note["message"]}
    if target.startswith("mobile_app_"):
        extra: dict[str, Any] = {
            "url": note["link"],
            "clickAction": note["link"],
            "group": "github-inbox",
            "tag": note["link"],
        }
        if note["url"]:
            extra["actions"] = [
                {"action": "URI", "title": "Open on GitHub", "uri": note["url"]}
            ]
        data["data"] = extra
    return data
