"""Plain data: items built from GitHub's JSON, the unseen rule and the panel's snapshot.

No Home Assistant imports, so all of it is tested on its own. Times are GitHub's UTC strings
(`2026-10-23T01:02:03Z`), which order correctly as strings.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from collections.abc import Iterable, Mapping, Set
from typing import Any

from .sanitize import preview, sanitize, text_html

Item = dict[str, Any]
Entry = dict[str, Any]

TIMELINE_KEEP = 200
LABEL_DEFAULT = "8b949e"
AGE_BUCKETS: tuple[tuple[str, float], ...] = (
    ("1 day", 1),
    ("1 week", 7),
    ("1 month", 30),
    ("3 months", 91),
    ("1 year", 365),
    ("older", float("inf")),
)
DAY_GROUPS = {
    "opened": "opened",
    "reopened": "opened",
    "commented": "commented",
    "reviewed": "reviewed",
    "closed": "closed",
    "merged": "closed",
}
DAYS = 14
RECENT = 25


def iso(when: dt.datetime) -> str:
    return when.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(value: Any) -> dt.datetime | None:
    if not value:
        return None
    try:
        t = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.UTC)


def login(user: Any) -> str:
    return str(user.get("login") or "") if isinstance(user, Mapping) else ""


def logins(values: Iterable[Any]) -> frozenset[str]:
    """Logins as compared: GitHub logins are case-insensitive."""
    return frozenset(str(v).strip().lower() for v in values if str(v).strip())


def is_me(who: str, me: Set[str]) -> bool:
    return who.lower() in me


def is_bot(user: Any) -> bool:
    if not isinstance(user, Mapping):
        return False
    return user.get("type") == "Bot" or login(user).endswith("[bot]")


def key_for(repo: str, number: int) -> str:
    return f"{repo}#{int(number)}"


def signature(markdown: Any) -> str:
    """Identifies a Markdown source, so a webhook's copy can be matched to a stored rendering."""
    return hashlib.sha256(str(markdown or "").encode()).hexdigest()[:16]


def labels(raw: Any) -> list[dict[str, str]]:
    out = []
    for label in raw or []:
        if not isinstance(label, Mapping) or not label.get("name"):
            continue
        color = str(label.get("color") or "").lower()
        if len(color) != 6 or any(c not in "0123456789abcdef" for c in color):
            color = LABEL_DEFAULT
        out.append({"name": str(label["name"]), "color": color})
    return out


def _body(obj: Mapping[str, Any]) -> tuple[str, str, bool]:
    """HTML, the source signature, and whether the HTML is GitHub's rendering."""
    rendered = sanitize(obj["body_html"]) if obj.get("body_html") else ""
    if rendered:
        return rendered, signature(obj.get("body")), True
    return text_html(obj.get("body")), signature(obj.get("body")), False


def _keeps_rendering(old: Mapping[str, Any], new: Mapping[str, Any]) -> bool:
    """A webhook's plain copy of an unchanged source does not replace GitHub's rendering."""
    return (
        new["body_sig"] == old.get("body_sig")
        and bool(old.get("body_rendered"))
        and not new.get("body_rendered")
    )


def _base(repo: str, obj: Mapping[str, Any], kind: str) -> Item:
    body_html, sig, rendered = _body(obj)
    user = obj.get("user")
    return {
        "key": key_for(repo, int(obj["number"])),
        "repo": repo,
        "number": int(obj["number"]),
        "kind": kind,
        "title": str(obj.get("title") or ""),
        "url": str(obj.get("html_url") or ""),
        "author": login(user),
        "author_bot": is_bot(user),
        "created_at": str(obj.get("created_at") or ""),
        "updated_at": str(obj.get("updated_at") or ""),
        "draft": bool(obj.get("draft")),
        "labels": labels(obj.get("labels")),
        "comments": int(obj.get("comments") or 0),
        "body_html": body_html,
        "body_sig": sig,
        "body_rendered": rendered,
        "preview": preview(body_html),
        "pr": None,
    }


def item_from_issue(repo: str, issue: Mapping[str, Any]) -> Item:
    """An item from an issue object; GitHub lists pull requests as issues too."""
    return _base(repo, issue, "pr" if issue.get("pull_request") else "issue")


def item_from_pull(repo: str, pull: Mapping[str, Any]) -> Item:
    """An item from a pull request. Review webhooks carry a short form without counts; merge keeps the stored ones."""
    item = _base(repo, pull, "pr")
    if "additions" not in pull:
        del item["comments"]
        return item
    item["comments"] = int(pull.get("comments") or 0) + int(
        pull.get("review_comments") or 0
    )
    head = pull.get("head")
    base = pull.get("base")
    item["pr"] = {
        "additions": int(pull.get("additions") or 0),
        "deletions": int(pull.get("deletions") or 0),
        "changed_files": int(pull.get("changed_files") or 0),
        "head": str(head.get("ref") or "") if isinstance(head, Mapping) else "",
        "base": str(base.get("ref") or "") if isinstance(base, Mapping) else "",
    }
    return item


def merge(old: Item | None, new: Item) -> Item:
    """The new summary over the stored item, keeping what the summary cannot carry."""
    if old is None:
        return {"comments": 0, **new, "timeline": [], "rev": 1}
    merged = {**old, **new}
    if _keeps_rendering(old, new):
        merged["body_html"] = old["body_html"]
        merged["body_rendered"] = True
        merged["preview"] = old.get("preview", new["preview"])
    if new["pr"] is None and old.get("pr"):
        merged["pr"] = old["pr"]
    merged["timeline"] = old.get("timeline", [])
    merged["rev"] = int(old.get("rev", 0)) + 1
    return merged


def _entry(obj: Mapping[str, Any], kind: str, prefix: str, at: Any) -> Entry:
    body_html, sig, rendered = _body(obj)
    user = obj.get("user")
    return {
        "id": f"{prefix}{obj.get('id')}",
        "kind": kind,
        "author": login(user),
        "author_bot": is_bot(user),
        "at": str(at or ""),
        "url": str(obj.get("html_url") or ""),
        "body_html": body_html,
        "body_sig": sig,
        "body_rendered": rendered,
        "preview": preview(body_html),
    }


def comment_entry(comment: Mapping[str, Any]) -> Entry:
    return _entry(comment, "comment", "c", comment.get("created_at"))


def review_entry(review: Mapping[str, Any]) -> Entry:
    entry = _entry(review, "review", "r", review.get("submitted_at"))
    entry["state"] = str(review.get("state") or "").lower()
    return entry


def review_comment_entry(comment: Mapping[str, Any]) -> Entry:
    entry = _entry(comment, "review_comment", "d", comment.get("created_at"))
    entry["path"] = str(comment.get("path") or "")
    line = comment.get("line") or comment.get("original_line")
    entry["line"] = int(line) if isinstance(line, int) else None
    return entry


def shown_review(review: Mapping[str, Any]) -> bool:
    """A review is shown when it decided something or says something; empty `commented` reviews only hold line comments."""
    state = str(review.get("state") or "").lower()
    if state == "pending":
        return False
    return state != "commented" or bool(str(review.get("body") or "").strip())


def timeline(
    comments: Iterable[Mapping[str, Any]],
    reviews: Iterable[Mapping[str, Any]] = (),
    review_comments: Iterable[Mapping[str, Any]] = (),
) -> list[Entry]:
    entries = [comment_entry(c) for c in comments]
    entries += [review_entry(r) for r in reviews if shown_review(r)]
    entries += [review_comment_entry(c) for c in review_comments]
    entries.sort(key=lambda e: (e["at"], e["id"]))
    return entries[-TIMELINE_KEEP:]


def upsert_entry(item: Item, entry: Entry) -> None:
    """Add or replace one entry, keeping GitHub's rendering when the source is unchanged."""
    entries: list[Entry] = item.setdefault("timeline", [])
    for i, old in enumerate(entries):
        if old["id"] == entry["id"]:
            if _keeps_rendering(old, entry):
                entry = {
                    **entry,
                    "body_html": old["body_html"],
                    "body_rendered": True,
                    "preview": old["preview"],
                }
            entries[i] = entry
            break
    else:
        entries.append(entry)
        entries.sort(key=lambda e: (e["at"], e["id"]))
        del entries[:-TIMELINE_KEEP]
    item["rev"] = int(item.get("rev", 0)) + 1


def remove_entry(item: Item, entry_id: str) -> None:
    entries: list[Entry] = item.get("timeline", [])
    item["timeline"] = [e for e in entries if e["id"] != entry_id]
    item["rev"] = int(item.get("rev", 0)) + 1


def activity_at(item: Item, me: Set[str]) -> str:
    """When someone other than you last opened, commented or reviewed; '' if never."""
    times = [item["created_at"]] if not is_me(item.get("author", ""), me) else []
    times += [
        e["at"] for e in item.get("timeline", []) if not is_me(e.get("author", ""), me)
    ]
    return max((t for t in times if t), default="")


def unseen(item: Item, seen: Mapping[str, str], me: Set[str]) -> bool:
    last = activity_at(item, me)
    return bool(last) and last > seen.get(item["key"], "")


def last_word(item: Item) -> tuple[str, str]:
    """Who spoke last on an item, and when: its newest timeline entry, or its author."""
    entries = item.get("timeline", [])
    if entries:
        return entries[-1]["author"], entries[-1]["at"]
    return item["author"], item["created_at"]


def summary(item: Item, is_unseen: bool, account: str, me: Set[str]) -> dict[str, Any]:
    """What the panel lists for an item; bodies come separately when it is opened."""
    entries = item.get("timeline", [])
    last = entries[-1] if entries else None
    who, last_at = last_word(item)
    return {
        "key": item["key"],
        "account": account,
        "me": sorted(me),
        "repo": item["repo"],
        "number": item["number"],
        "kind": item["kind"],
        "title": item["title"],
        "url": item["url"],
        "author": item["author"],
        "author_bot": item["author_bot"],
        "created_at": item["created_at"],
        "updated_at": item["updated_at"],
        "draft": item["draft"],
        "labels": item["labels"],
        "comments": max(item.get("comments", 0), len(entries)),
        "preview": item.get("preview", ""),
        "pr": item.get("pr"),
        "rev": item.get("rev", 0),
        "unseen": is_unseen,
        "waiting": not is_me(who, me),
        "last_at": last_at,
        "last": (
            {
                "author": last["author"],
                "at": last["at"],
                "kind": last["kind"],
                "state": last.get("state"),
                "preview": last["preview"],
            }
            if last
            else None
        ),
    }


def _age_bucket(created: dt.datetime | None, now: dt.datetime) -> int:
    days = (now - created).total_seconds() / 86400 if created else 0.0
    for i, (_, limit) in enumerate(AGE_BUCKETS):
        if days < limit:
            return i
    return len(AGE_BUCKETS) - 1


CI_FAILURES = {"failure", "timed_out", "startup_failure"}
CI_PASSING = {"success", "skipped", "neutral"}


def ci_run(raw: Mapping[str, Any]) -> dict[str, Any]:
    """The fields shown for one workflow's latest default-branch run."""
    status = str(raw.get("status") or "")
    if status in ("requested", "waiting", "pending"):
        status = "queued"
    return {
        "workflow_id": int(raw["workflow_id"]),
        "name": str(raw.get("name") or "Workflow"),
        "id": int(raw["id"]),
        "html_url": str(raw.get("html_url") or ""),
        "status": status,
        "conclusion": str(raw.get("conclusion") or ""),
        "head_sha": str(raw.get("head_sha") or ""),
        "event": str(raw.get("event") or ""),
        "updated_at": str(raw.get("updated_at") or ""),
    }


def newer_ci_run(new: Mapping[str, Any], old: Mapping[str, Any]) -> bool:
    return (int(new["id"]), str(new["updated_at"])) > (
        int(old["id"]),
        str(old["updated_at"]),
    )


def ci_workflows(
    rows: Iterable[Mapping[str, Any]], branch: str
) -> dict[str, dict[str, Any]]:
    workflows: dict[str, dict[str, Any]] = {}
    for raw in rows:
        if (
            raw.get("head_branch") != branch
            or not raw.get("workflow_id")
            or not raw.get("id")
        ):
            continue
        run = ci_run(raw)
        key = str(run["workflow_id"])
        if key not in workflows or newer_ci_run(run, workflows[key]):
            workflows[key] = run
    return workflows


def ci_state(workflows: Mapping[str, Mapping[str, Any]]) -> str:
    if not workflows:
        return "no_ci"
    runs = list(workflows.values())
    if any(r["status"] == "completed" and r["conclusion"] in CI_FAILURES for r in runs):
        return "failing"
    if any(r["status"] in ("queued", "in_progress") for r in runs):
        return "running"
    if all(r["status"] == "completed" and r["conclusion"] in CI_PASSING for r in runs):
        return "passing"
    if any(r["conclusion"] == "action_required" for r in runs):
        return "action_required"
    return "cancelled"


def snapshot(accounts: Iterable[Mapping[str, Any]], now: dt.datetime) -> dict[str, Any]:
    """Everything the panel draws, from each account's stored state.

    An account mapping holds `account`, `me` (your logins), `items`, `seen`, `activity`, `repos` (count installed)
    and `sync`.
    """
    items: list[dict[str, Any]] = []
    activity: list[dict[str, Any]] = []
    syncs: list[dict[str, Any]] = []
    repos_installed = 0
    ci: list[dict[str, Any]] = []
    ci_permissions: list[dict[str, Any]] = []
    for acc in accounts:
        account = str(acc["account"])
        me: Set[str] = acc["me"]
        for item in acc["items"].values():
            items.append(summary(item, unseen(item, acc["seen"], me), account, me))
        activity += acc["activity"]
        repos_installed += int(acc["repos"])
        for repo in acc.get("installed", []):
            stored = acc.get("ci", {}).get(repo, {})
            workflows = list(stored.get("workflows", {}).values())
            workflows.sort(
                key=lambda r: (
                    r["status"] != "completed" or r["conclusion"] not in CI_FAILURES,
                    r["name"],
                )
            )
            ci.append(
                {
                    "repo": repo,
                    "account": account,
                    "default_branch": stored.get("default_branch", ""),
                    "state": ci_state(stored.get("workflows", {})),
                    "unreadable": bool(stored.get("unreadable")),
                    "workflows": workflows,
                    "newest_at": max((r["updated_at"] for r in workflows), default=""),
                }
            )
        if acc.get("ci_permission"):
            ci_permissions.append({"account": account, **acc["ci_permission"]})
        syncs.append({"account": account, **acc["sync"]})
    items.sort(key=lambda i: i["updated_at"], reverse=True)
    ci.sort(
        key=lambda r: (
            {
                "failing": 0,
                "running": 1,
                "passing": 2,
                "cancelled": 3,
                "action_required": 3,
                "no_ci": 4,
            }[r["state"]],
            r["repo"],
        )
    )

    by_repo: dict[str, dict[str, Any]] = {}
    ages: list[dict[str, Any]] = [
        {"label": label, "issues": 0, "prs": 0} for label, _ in AGE_BUCKETS
    ]
    for i in items:
        row = by_repo.setdefault(
            i["repo"],
            {"repo": i["repo"], "issues": 0, "prs": 0, "drafts": 0, "unseen": 0},
        )
        if i["kind"] == "pr":
            row["drafts" if i["draft"] else "prs"] += 1
        else:
            row["issues"] += 1
        row["unseen"] += int(i["unseen"])
        bucket = ages[_age_bucket(parse(i["created_at"]), now)]
        bucket["prs" if i["kind"] == "pr" else "issues"] += 1
    repo_rows = sorted(
        by_repo.values(),
        key=lambda r: (-(r["issues"] + r["prs"] + r["drafts"]), r["repo"]),
    )

    tz = now.tzinfo or dt.UTC
    today = now.astimezone(tz).date()
    days: list[dict[str, Any]] = [
        {
            "date": (today - dt.timedelta(days=DAYS - 1 - n)).isoformat(),
            "opened": 0,
            "commented": 0,
            "reviewed": 0,
            "closed": 0,
        }
        for n in range(DAYS)
    ]
    index = {d["date"]: d for d in days}
    for a in activity:
        when = parse(a.get("at"))
        group = DAY_GROUPS.get(str(a.get("type")))
        if when is None or group is None:
            continue
        day = index.get(when.astimezone(tz).date().isoformat())
        if day is not None:
            day[group] += 1
    recent = sorted(
        (a for a in activity if parse(a.get("at"))),
        key=lambda a: str(a["at"]),
        reverse=True,
    )[:RECENT]

    oldest = min(items, key=lambda i: i["created_at"] or "~", default=None)
    issues = sum(1 for i in items if i["kind"] == "issue")
    prs = [i for i in items if i["kind"] == "pr"]
    return {
        "generated": iso(now),
        "items": items,
        "totals": {
            "issues": issues,
            "prs": len(prs),
            "drafts": sum(1 for i in prs if i["draft"]),
            "unseen": sum(1 for i in items if i["unseen"]),
            "waiting": sum(1 for i in items if i["waiting"]),
            "repos": len(repo_rows),
            "repos_installed": repos_installed,
            "activity_7d": sum(
                sum(d[g] for g in ("opened", "commented", "reviewed", "closed"))
                for d in days[-7:]
            ),
        },
        "oldest": (
            {"key": oldest["key"], "created_at": oldest["created_at"]}
            if oldest
            else None
        ),
        "by_repo": repo_rows,
        "ages": ages,
        "days": days,
        "recent": recent,
        "sync": syncs,
        "ci": ci,
        "ci_totals": {
            state: sum(r["state"] == state for r in ci)
            for state in ("failing", "running", "passing")
        },
        "ci_permissions": ci_permissions,
    }
