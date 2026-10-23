"""GitHub JSON shaped like the REST API and webhook payloads, trimmed to the fields the inbox reads."""

from __future__ import annotations

from typing import Any

REPO = "heidrickla/ha-glkvm"


def user(login: str = "someone") -> dict[str, Any]:
    return {
        "login": login,
        "id": 1000,
        "type": "Bot" if login.endswith("[bot]") else "User",
    }


def issue(
    number: int,
    *,
    title: str = "ATX buttons fail with HTTP 500",
    author: str = "someone",
    body: str = "Hi **there**",
    body_html: str = "<p>Hi <strong>there</strong></p>",
    created: str = "2026-10-20T10:00:00Z",
    updated: str = "2026-10-21T10:00:00Z",
    comments: int = 0,
    pull_request: bool = False,
    state: str = "open",
    repo: str = REPO,
) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "number": number,
        "title": title,
        "html_url": f"https://github.com/{repo}/issues/{number}",
        "user": user(author),
        "state": state,
        "body": body,
        "body_html": body_html,
        "created_at": created,
        "updated_at": updated,
        "comments": comments,
        "labels": [{"name": "bug", "color": "d73a4a"}],
    }
    if pull_request:
        raw["pull_request"] = {
            "url": f"https://api.github.com/repos/{repo}/pulls/{number}"
        }
        raw["draft"] = False
    return raw


def pull(
    number: int,
    *,
    title: str = "Poll the ATX busy flag",
    author: str = "someone",
    draft: bool = False,
    created: str = "2026-10-20T10:00:00Z",
    updated: str = "2026-10-21T10:00:00Z",
    state: str = "open",
    merged: bool = False,
    repo: str = REPO,
) -> dict[str, Any]:
    return {
        "number": number,
        "title": title,
        "html_url": f"https://github.com/{repo}/pull/{number}",
        "user": user(author),
        "state": state,
        "merged": merged,
        "draft": draft,
        "body": "Fixes #2",
        "body_html": "<p>Fixes #2</p>",
        "created_at": created,
        "updated_at": updated,
        "labels": [],
        "comments": 1,
        "review_comments": 2,
        "additions": 12,
        "deletions": 3,
        "changed_files": 2,
        "head": {"ref": "fix"},
        "base": {"ref": "main"},
    }


def comment(
    cid: int,
    *,
    author: str = "someone",
    body: str = "Thanks",
    body_html: str = "<p>Thanks</p>",
    at: str = "2026-10-21T10:00:00Z",
) -> dict[str, Any]:
    return {
        "id": cid,
        "user": user(author),
        "body": body,
        "body_html": body_html,
        "created_at": at,
        "updated_at": at,
        "html_url": f"https://github.com/{REPO}/issues/2#issuecomment-{cid}",
    }


def review(
    rid: int,
    state: str,
    *,
    author: str = "reviewer",
    body: str = "Looks good",
    at: str = "2026-10-21T11:00:00Z",
) -> dict[str, Any]:
    return {
        "id": rid,
        "user": user(author),
        "state": state,
        "body": body,
        "body_html": f"<p>{body}</p>" if body else "",
        "submitted_at": at,
        "html_url": f"https://github.com/{REPO}/pull/5#pullrequestreview-{rid}",
    }


def review_comment(
    cid: int, *, author: str = "reviewer", at: str = "2026-10-21T11:00:00Z"
) -> dict[str, Any]:
    return {
        "id": cid,
        "user": user(author),
        "body": "Rename this",
        "body_html": "<p>Rename this</p>",
        "created_at": at,
        "path": "custom_components/glkvm/api.py",
        "line": 42,
        "pull_request_review_id": 8,
        "html_url": f"https://github.com/{REPO}/pull/5#discussion_r{cid}",
    }
