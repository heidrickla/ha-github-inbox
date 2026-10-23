"""The inbox for one GitHub account: applies webhook events, syncs with GitHub and sends notifications.

Webhooks keep the inbox current. A full sync runs at setup, when GitHub reports repositories added or renamed, after
the cloud connection comes back and on the resync action; it fetches only the items that changed since the stored copy,
and anything it finds that no webhook delivered is notified as one message.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from collections.abc import Callable, Mapping
from typing import Any, override

from homeassistant.components import persistent_notification
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from . import model, notes
from .api import (
    GitHubAuthError,
    GitHubError,
    GitHubForbiddenError,
    GitHubNotFoundError,
    InstallationClient,
    Listing,
)
from .const import (
    ACTIVITY_CLOSED,
    ACTIVITY_COMMENTED,
    ACTIVITY_DAYS,
    ACTIVITY_KEEP,
    ACTIVITY_MERGED,
    ACTIVITY_OPENED,
    ACTIVITY_REOPENED,
    ACTIVITY_REVIEWED,
    CONF_ACCOUNT,
    CONF_ACCOUNT_ID,
    CONF_APP_URL,
    CONF_INCLUDE_BOTS,
    CONF_INCLUDE_OWN,
    CONF_INSTALLATION_ID,
    CONF_ME,
    CONF_NOTIFY_ON,
    CONF_NOTIFY_TARGETS,
    CONF_PERSISTENT,
    DEFAULT_NOTIFY_ON,
    DELIVERY_KEEP,
    DOMAIN,
    NOTIFY_DELAY,
    REFRESH_DELAY,
    SIGNAL_UPDATED,
    SYNC_RETRY,
)
from .model import Item
from .store import InboxStore

_LOGGER = logging.getLogger(__name__)

# Parallel GitHub reads during a sync.
CONCURRENCY = 6
REMOVED_REPO_ACTIONS = ("deleted", "archived", "transferred")
RESYNC_REPO_ACTIONS = ("renamed", "unarchived", "created")
# Stored bookkeeping the panel does not need.
INTERNAL = ("body_sig", "body_rendered", "synced")
# Seconds a notify call may take before the next target is tried.
NOTIFY_TIMEOUT = 30

type InboxConfigEntry = ConfigEntry[InboxCoordinator]


class InboxCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Holds the inbox; `data` is the counts the sensors show."""

    config_entry: InboxConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: InboxConfigEntry,
        client: InstallationClient,
        store: InboxStore,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.title}",
            update_interval=None,
        )
        self.client = client
        self.store = store
        self.account = str(entry.data[CONF_ACCOUNT])
        # You on GitHub: the account itself, or the people named for an organization's inbox.
        self.me = model.logins(entry.options.get(CONF_ME) or [self.account])
        self.sync_state = "pending"
        self.sync_error: str | None = None
        self.installation_lost = False
        self._closed = False
        self._refresh_warned = False
        self.data = self._counts()
        self._gen: dict[str, int] = {}
        self._refresh_cancel: dict[str, CALLBACK_TYPE] = {}
        self._notes: list[dict[str, Any]] = []
        self._notes_cancel: CALLBACK_TYPE | None = None
        self._sync_cancel: CALLBACK_TYPE | None = None
        self._retry = 0
        self._failed_targets: set[str] = set()
        self._activity_listeners: list[Callable[[dict[str, Any]], None]] = []
        self._handlers: dict[str, Callable[[dict[str, Any]], None]] = {
            "issues": self._on_issues,
            "issue_comment": self._on_issue_comment,
            "pull_request": self._on_pull_request,
            "pull_request_review": self._on_review,
            "pull_request_review_comment": self._on_review_comment,
            "installation": self._on_installation,
            "installation_repositories": self._on_installation_repositories,
            "repository": self._on_repository,
        }

    # ------------------------------------------------------------- state

    @property
    def items(self) -> dict[str, Item]:
        items: dict[str, Item] = self.store.data["items"]
        return items

    def _counts(self) -> dict[str, Any]:
        last_delivery = self.store.data["last_delivery"]
        if self.store.data["last_sync"] is None:
            # Nothing has been read from GitHub yet: the counts are unknown, not zero.
            return {
                "issues": None,
                "prs": None,
                "drafts": None,
                "unseen": None,
                "last_delivery": last_delivery,
            }
        items = self.items.values()
        seen = self.store.data["seen"]
        return {
            "issues": sum(1 for i in items if i["kind"] == "issue"),
            "prs": sum(1 for i in items if i["kind"] == "pr"),
            "drafts": sum(1 for i in items if i["kind"] == "pr" and i["draft"]),
            "unseen": sum(1 for i in items if model.unseen(i, seen, self.me)),
            "last_delivery": last_delivery,
        }

    def account_view(self) -> dict[str, Any]:
        """This account's state for the panel snapshot."""
        return {
            "account": self.account,
            "me": self.me,
            "items": self.items,
            "seen": self.store.data["seen"],
            "activity": self.store.data["activity"],
            "repos": len(self.store.data["repos"]),
            "sync": {
                "entry_id": self.config_entry.entry_id,
                "state": "lost" if self.installation_lost else self.sync_state,
                "error": self.sync_error,
                "last_sync": self.store.data["last_sync"],
                "last_delivery": self.store.data["last_delivery"],
                "app_url": self.config_entry.data.get(CONF_APP_URL, ""),
            },
        }

    @callback
    def _publish(self) -> None:
        """Counts to the entities, a snapshot to the panel, the state to disk."""
        self.data = self._counts()
        self.async_update_listeners()
        async_dispatcher_send(self.hass, SIGNAL_UPDATED)
        if not self._closed:
            # After unload the store has been flushed; a late refresh must not schedule another write.
            self.store.async_save()

    @callback
    def async_add_activity_listener(
        self, listener: Callable[[dict[str, Any]], None]
    ) -> CALLBACK_TYPE:
        self._activity_listeners.append(listener)

        @callback
        def remove() -> None:
            self._activity_listeners.remove(listener)

        return remove

    @callback
    def async_mark_seen(self, keys: list[str] | None) -> None:
        """Mark items seen; None marks every item."""
        now = model.iso(dt_util.utcnow())
        seen: dict[str, str] = self.store.data["seen"]
        for key in self.items if keys is None else keys:
            if key in self.items:
                seen[key] = max(now, model.activity_at(self.items[key], self.me))
        self._publish()

    # -------------------------------------------------------------- sync

    @override
    async def _async_update_data(self) -> dict[str, Any]:
        if self._sync_cancel:
            self._sync_cancel()
            self._sync_cancel = None
        self.sync_state = "syncing"
        async_dispatcher_send(self.hass, SIGNAL_UPDATED)
        try:
            await self._sync()
        except GitHubAuthError as err:
            self._failed(err)
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="auth_failed"
            ) from err
        except GitHubError as err:
            self._failed(err)
            self._schedule_sync(SYNC_RETRY[min(self._retry, len(SYNC_RETRY) - 1)])
            self._retry += 1
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="sync_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        except Exception as err:
            self._failed(err)
            raise
        self._retry = 0
        self.sync_state = "ok"
        self.sync_error = None
        self.installation_lost = False
        self._publish()
        return self._counts()

    @callback
    def _failed(self, err: Exception) -> None:
        self.sync_state = "failed"
        self.sync_error = str(err) or type(err).__name__
        async_dispatcher_send(self.hass, SIGNAL_UPDATED)

    async def _sync(self) -> None:
        start_gen = dict(self._gen)
        live = self.items
        installed = await self.client.repositories()
        names = sorted(
            str(r["full_name"])
            for r in installed.rows
            if r.get("full_name") and not r.get("archived")
        )
        # Repositories whose open items were not all read: their stored items stay.
        partial: set[str] = set()
        if not installed.complete:
            listed = set(names)
            partial |= {i["repo"] for i in live.values() if i["repo"] not in listed}
            _LOGGER.warning(
                "GitHub listed more than %s repositories for %s; items of the rest are kept as stored",
                len(installed.rows),
                self.account,
            )
        gate = asyncio.Semaphore(CONCURRENCY)

        async def listing(repo: str) -> Listing | None:
            async with gate:
                try:
                    return await self.client.open_issues(repo)
                except GitHubNotFoundError:
                    # Gone, disabled or blocked: it has no items.
                    return Listing([], complete=True)
                except GitHubForbiddenError as err:
                    # Refused: the items may still be open, so the stored copy stays.
                    _LOGGER.warning("GitHub refused to list %s: %s", repo, err)
                    return None

        lists = await asyncio.gather(*(listing(n) for n in names))
        fresh: dict[str, Item] = {}
        for repo, issues in zip(names, lists, strict=True):
            if issues is None or not issues.complete:
                partial.add(repo)
            for issue in issues.rows if issues is not None else []:
                if issue.get("number") is not None:
                    item = model.item_from_issue(repo, issue)
                    fresh[item["key"]] = item

        first = self.store.data["last_sync"] is None
        since = self.store.data["last_sync"] or ""
        changed = [
            k
            for k, item in fresh.items()
            if k not in live
            or live[k].get("updated_at") != item["updated_at"]
            or not live[k].get("synced")
        ]
        olds = {k: live.get(k) for k in changed}

        async def details(key: str) -> Item:
            async with gate:
                return await self._details(model.merge(olds[key], fresh[key]))

        updated = dict(
            zip(
                changed,
                await asyncio.gather(*(details(k) for k in changed)),
                strict=True,
            )
        )

        def touched(key: str) -> bool:
            return self._gen.get(key, 0) != start_gen.get(key, 0)

        # A webhook that touched an item while the sync read GitHub is newer than the sync.
        result: dict[str, Item] = {}
        missed: list[dict[str, Any]] = []
        for key in fresh:
            if touched(key):
                if key in live:
                    result[key] = live[key]
            elif key in updated:
                if not first:
                    missed += self._missed(olds[key], updated[key], since)
                result[key] = updated[key]
            else:
                result[key] = live[key]
        for key, item in live.items():
            if key not in fresh and (touched(key) or item["repo"] in partial):
                result[key] = item
        self.store.data["items"] = result
        seen: dict[str, str] = self.store.data["seen"]
        self.store.data["seen"] = {k: v for k, v in seen.items() if k in result}
        self.store.data["repos"] = sorted(set(names) | partial)
        self.store.data["last_sync"] = model.iso(dt_util.utcnow())
        for entry in missed:
            self._record(entry)
        self._queue(missed, missed=True)

    def _missed(self, old: Item | None, new: Item, since: str) -> list[dict[str, Any]]:
        """Activity a sync found that no webhook delivered."""
        out = []
        if old is None and new["created_at"] > since:
            out.append(
                self._entry(
                    ACTIVITY_OPENED,
                    new,
                    {
                        "login": new["author"],
                        "type": "Bot" if new["author_bot"] else "",
                    },
                    preview_text=new.get("preview", ""),
                    at=new["created_at"],
                )
            )
        known = {e["id"] for e in (old or {}).get("timeline", [])}
        for e in new.get("timeline", []):
            if e["id"] in known or e["at"] <= since:
                continue
            kind = ACTIVITY_REVIEWED if e["kind"] == "review" else ACTIVITY_COMMENTED
            if e["kind"] == "review_comment":
                continue
            out.append(
                self._entry(
                    kind,
                    new,
                    {"login": e["author"], "type": "Bot" if e["author_bot"] else ""},
                    preview_text=e["preview"],
                    state=e.get("state"),
                    at=e["at"],
                )
            )
        return out

    async def _details(self, item: Item, pull: dict[str, Any] | None = None) -> Item:
        """The item with its pull request details and its whole timeline."""
        repo, number = item["repo"], item["number"]
        if item["kind"] == "pr":
            reads = await asyncio.gather(
                self.client.pull(repo, number) if pull is None else _ready(pull),
                self.client.issue_comments(repo, number),
                self.client.reviews(repo, number),
                self.client.review_comments(repo, number),
            )
            pull_obj, comments, reviews, review_comments = reads
            item = model.merge(item, model.item_from_pull(repo, pull_obj))
            item["timeline"] = model.timeline(comments, reviews, review_comments)
        else:
            comments = (
                await self.client.issue_comments(repo, number)
                if item["comments"]
                else []
            )
            item["timeline"] = model.timeline(comments)
        item["synced"] = True
        return item

    @callback
    def schedule_sync(self) -> None:
        self._schedule_sync(REFRESH_DELAY)

    @callback
    def _schedule_sync(self, delay: float) -> None:
        if self._sync_cancel:
            self._sync_cancel()

        @callback
        def run(_now: dt.datetime) -> None:
            self._sync_cancel = None
            self.config_entry.async_create_background_task(
                self.hass, self.async_refresh(), f"{DOMAIN} sync"
            )

        self._sync_cancel = async_call_later(self.hass, delay, run)

    # ---------------------------------------------------------- webhooks

    @callback
    def async_receive(self, event: str, delivery: str, payload: dict[str, Any]) -> None:
        """Apply one verified webhook delivery."""
        deliveries: list[str] = self.store.data["deliveries"]
        if delivery and delivery in deliveries:
            return
        if delivery:
            deliveries.append(delivery)
            del deliveries[:-DELIVERY_KEEP]
        self.store.data["last_delivery"] = model.iso(dt_util.utcnow())
        handler = self._handlers.get(event)
        if handler is not None:
            try:
                handler(payload)
            except (KeyError, TypeError, ValueError) as err:
                _LOGGER.warning(
                    "Ignored a %s delivery GitHub sent without the expected fields: %s",
                    event,
                    type(err).__name__,
                )
        self._publish()

    def _touch(self, key: str) -> int:
        self._gen[key] = self._gen.get(key, 0) + 1
        return self._gen[key]

    def _upsert(self, item: Item) -> Item:
        self._touch(item["key"])
        merged = model.merge(self.items.get(item["key"]), item)
        self.items[item["key"]] = merged
        return merged

    def _remove(self, key: str) -> None:
        self._touch(key)
        if cancel := self._refresh_cancel.pop(key, None):
            cancel()
        self.items.pop(key, None)
        self.store.data["seen"].pop(key, None)

    @callback
    def _schedule_item_refresh(self, repo: str, number: int, kind: str) -> None:
        key = model.key_for(repo, number)
        if cancel := self._refresh_cancel.pop(key, None):
            cancel()

        @callback
        def run(_now: dt.datetime) -> None:
            self._refresh_cancel.pop(key, None)
            self.config_entry.async_create_background_task(
                self.hass,
                self._refresh_item(repo, number, kind),
                f"{DOMAIN} refresh {key}",
            )

        self._refresh_cancel[key] = async_call_later(self.hass, REFRESH_DELAY, run)

    async def _refresh_item(self, repo: str, number: int, kind: str) -> None:
        """Replace an item with GitHub's own copy: rendered bodies, every comment."""
        key = model.key_for(repo, number)
        gen = self._gen.get(key, 0)
        try:
            if kind == "pr":
                obj = await self.client.pull(repo, number)
                item = model.item_from_pull(repo, obj)
            else:
                obj = await self.client.issue(repo, number)
                item = model.item_from_issue(repo, obj)
            if obj.get("state") == "open":
                item = await self._details(
                    model.merge(self.items.get(key), item),
                    pull=obj if kind == "pr" else None,
                )
        except GitHubNotFoundError:
            obj, item = {"state": "gone"}, {}
        except GitHubAuthError as err:
            # The key or installation stopped working; the sync's failure starts re-authentication.
            _LOGGER.warning("GitHub refused the app while reading %s: %s", key, err)
            self.schedule_sync()
            return
        except GitHubError as err:
            if not self._refresh_warned:
                _LOGGER.warning("Kept the webhook copy of %s: %s", key, err)
                self._refresh_warned = True
            return
        if self._refresh_warned:
            _LOGGER.info("Reading items from GitHub works again")
            self._refresh_warned = False
        if self._gen.get(key, 0) != gen:
            # A newer event touched the item while this read ran.
            return
        if obj.get("state") != "open":
            self._remove(key)
        else:
            self.items[key] = item
        self._publish()

    def _record(self, entry: dict[str, Any]) -> None:
        log: list[dict[str, Any]] = self.store.data["activity"]
        log.append(entry)
        cutoff = model.iso(dt_util.utcnow() - dt.timedelta(days=ACTIVITY_DAYS))
        log[:] = [a for a in log if a["at"] >= cutoff][-ACTIVITY_KEEP:]
        for listener in list(self._activity_listeners):
            listener(entry)

    def _entry(
        self,
        kind: str,
        item: Mapping[str, Any],
        actor: Any,
        *,
        preview_text: str = "",
        state: str | None = None,
        at: str | None = None,
    ) -> dict[str, Any]:
        who = model.login(actor)
        return {
            "at": at or model.iso(dt_util.utcnow()),
            "type": kind,
            "key": item["key"],
            "repo": item["repo"],
            "number": item["number"],
            "kind": item["kind"],
            "title": item["title"],
            "url": item["url"],
            "actor": who,
            "bot": model.is_bot(actor),
            "own": model.is_me(who, self.me),
            "state": state,
            "preview": preview_text,
        }

    def _activity(
        self,
        kind: str,
        item: Mapping[str, Any],
        actor: Any,
        *,
        preview_text: str = "",
        state: str | None = None,
        url: str | None = None,
    ) -> None:
        entry = self._entry(kind, item, actor, preview_text=preview_text, state=state)
        if url:
            entry["url"] = url
        self._record(entry)
        self._queue([entry])

    def _on_issues(self, p: dict[str, Any]) -> None:
        repo = str(p["repository"]["full_name"])
        issue = p["issue"]
        item = model.item_from_issue(repo, issue)
        action = p.get("action")
        if action in ("closed", "deleted", "transferred"):
            self._remove(item["key"])
            if action == "closed":
                self._activity(ACTIVITY_CLOSED, item, p.get("sender"))
            return
        if issue.get("state") != "open":
            # A label, lock or edit on a closed issue keeps it out of the inbox.
            self._remove(item["key"])
            return
        stored = self._upsert(item)
        if action in ("opened", "reopened"):
            kind = ACTIVITY_OPENED if action == "opened" else ACTIVITY_REOPENED
            self._activity(
                kind, stored, p.get("sender"), preview_text=stored["preview"]
            )
        self._schedule_item_refresh(repo, item["number"], "issue")

    def _on_pull_request(self, p: dict[str, Any]) -> None:
        repo = str(p["repository"]["full_name"])
        pull = p["pull_request"]
        item = model.item_from_pull(repo, pull)
        action = p.get("action")
        if action == "closed":
            self._remove(item["key"])
            kind = ACTIVITY_MERGED if pull.get("merged") else ACTIVITY_CLOSED
            self._activity(kind, item, p.get("sender"))
            return
        if pull.get("state") != "open":
            self._remove(item["key"])
            return
        stored = self._upsert(item)
        if action in ("opened", "reopened"):
            kind = ACTIVITY_OPENED if action == "opened" else ACTIVITY_REOPENED
            self._activity(
                kind, stored, p.get("sender"), preview_text=stored["preview"]
            )
        self._schedule_item_refresh(repo, item["number"], "pr")

    def _open_item(self, repo: str, obj: dict[str, Any], kind: str) -> Item | None:
        """The stored item for a comment's parent, created from the payload if new; None when it is closed."""
        if obj.get("state") != "open":
            return None
        if kind == "pr" and "head" in obj:
            return self._upsert(model.item_from_pull(repo, obj))
        return self._upsert(model.item_from_issue(repo, obj))

    def _on_issue_comment(self, p: dict[str, Any]) -> None:
        repo = str(p["repository"]["full_name"])
        issue = p["issue"]
        kind = "pr" if issue.get("pull_request") else "issue"
        item = self._open_item(repo, issue, kind)
        if item is None:
            return
        entry = model.comment_entry(p["comment"])
        if p.get("action") == "deleted":
            model.remove_entry(item, entry["id"])
        else:
            model.upsert_entry(item, entry)
            if p.get("action") == "created":
                self._activity(
                    ACTIVITY_COMMENTED,
                    item,
                    p["comment"].get("user"),
                    preview_text=entry["preview"],
                    url=entry["url"],
                )
        self._schedule_item_refresh(repo, item["number"], kind)

    def _on_review(self, p: dict[str, Any]) -> None:
        repo = str(p["repository"]["full_name"])
        item = self._open_item(repo, p["pull_request"], "pr")
        if item is None:
            return
        review = p["review"]
        entry = model.review_entry(review)
        if model.shown_review(review):
            model.upsert_entry(item, entry)
        if p.get("action") == "submitted":
            self._activity(
                ACTIVITY_REVIEWED,
                item,
                review.get("user"),
                preview_text=entry["preview"],
                state=entry["state"],
                url=entry["url"],
            )
        self._schedule_item_refresh(repo, item["number"], "pr")

    def _on_review_comment(self, p: dict[str, Any]) -> None:
        # Line comments arrive with their review, which is what is notified.
        repo = str(p["repository"]["full_name"])
        item = self._open_item(repo, p["pull_request"], "pr")
        if item is None:
            return
        entry = model.review_comment_entry(p["comment"])
        if p.get("action") == "deleted":
            model.remove_entry(item, entry["id"])
        else:
            model.upsert_entry(item, entry)
        self._schedule_item_refresh(repo, item["number"], "pr")

    def _rename_repo(self, old: str, new: str) -> None:
        """Move items, seen marks and activity to a renamed repository; the sync then reads their new URLs."""
        seen: dict[str, str] = self.store.data["seen"]
        for key in [k for k, i in self.items.items() if i["repo"] == old]:
            item = self.items.pop(key)
            self._touch(key)
            item["repo"] = new
            item["key"] = model.key_for(new, item["number"])
            self.items[item["key"]] = item
            self._touch(item["key"])
            if key in seen:
                seen[item["key"]] = seen.pop(key)
            if cancel := self._refresh_cancel.pop(key, None):
                cancel()
        for entry in self.store.data["activity"]:
            if entry.get("repo") == old:
                entry["repo"] = new
                entry["key"] = model.key_for(new, entry["number"])
        repos: list[str] = self.store.data["repos"]
        if old in repos:
            repos[repos.index(old)] = new

    def _drop_repo(self, repo: str) -> None:
        for key in [k for k, i in self.items.items() if i["repo"] == repo]:
            self._remove(key)
        repos: list[str] = self.store.data["repos"]
        if repo in repos:
            repos.remove(repo)

    def _on_installation(self, p: dict[str, Any]) -> None:
        action = p.get("action")
        entry = self.config_entry
        installation = p.get("installation") or {}
        new_id = int(installation.get("id") or 0)
        current = int(entry.data[CONF_INSTALLATION_ID])
        if action in ("deleted", "suspend"):
            if new_id in (0, current):
                if not self.installation_lost:
                    _LOGGER.warning(
                        "GitHub reports the app %s for %s; the inbox is unavailable until it is installed again",
                        "uninstalled" if action == "deleted" else "suspended",
                        self.account,
                    )
                self.installation_lost = True
                entry.async_start_reauth(self.hass)
            return
        if action not in ("created", "unsuspend", "new_permissions_accepted"):
            return
        account = installation.get("account") or {}
        if int(account.get("id") or 0) != int(entry.data[CONF_ACCOUNT_ID]):
            return
        if self.installation_lost:
            _LOGGER.info("GitHub reports the app installed again for %s", self.account)
        self.installation_lost = False
        for flow in self.hass.config_entries.flow.async_progress_by_handler(
            DOMAIN, match_context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id}
        ):
            self.hass.config_entries.flow.async_abort(flow["flow_id"])
        if new_id and new_id != current:
            # Reinstalled: the signed delivery names the new installation.
            self.hass.config_entries.async_update_entry(
                entry, data={**entry.data, CONF_INSTALLATION_ID: new_id}
            )
            self.hass.config_entries.async_schedule_reload(entry.entry_id)
            return
        self.schedule_sync()

    def _on_installation_repositories(self, p: dict[str, Any]) -> None:
        for repo in p.get("repositories_removed") or []:
            self._drop_repo(str(repo["full_name"]))
        if p.get("repositories_added"):
            self.schedule_sync()

    def _on_repository(self, p: dict[str, Any]) -> None:
        action = p.get("action")
        repo = str(p["repository"]["full_name"])
        if action in REMOVED_REPO_ACTIONS:
            if action == "transferred" and (p.get("changes") or {}).get("owner"):
                # The webhook names the repository by its new owner; the stored name is the old one.
                old = p["changes"]["owner"]["from"]
                owner = old.get("user") or old.get("organization") or {}
                name = str(p["repository"]["name"])
                self._drop_repo(f"{owner.get('login', '')}/{name}")
            self._drop_repo(repo)
        elif action in RESYNC_REPO_ACTIONS:
            if action == "renamed":
                old_name = (
                    (p.get("changes") or {}).get("repository", {}).get("name", {})
                )
                prefix = repo.rsplit("/", 1)[0]
                if old_name.get("from"):
                    self._rename_repo(f"{prefix}/{old_name['from']}", repo)
            self.schedule_sync()

    # ----------------------------------------------------- notifications

    def _wanted(self, entry: Mapping[str, Any]) -> bool:
        opts = self.config_entry.options
        if entry["own"] and not opts.get(CONF_INCLUDE_OWN, False):
            return False
        if entry["bot"] and not opts.get(CONF_INCLUDE_BOTS, False):
            return False
        group = notes.GROUP.get(str(entry["type"]))
        return group in opts.get(CONF_NOTIFY_ON, DEFAULT_NOTIFY_ON)

    def _queue(self, entries: list[dict[str, Any]], missed: bool = False) -> None:
        opts = self.config_entry.options
        if not opts.get(CONF_NOTIFY_TARGETS) and not opts.get(CONF_PERSISTENT):
            return
        wanted = [e for e in entries if self._wanted(e)]
        if not wanted:
            return
        if missed:
            self.config_entry.async_create_background_task(
                self.hass, self._send(wanted, missed=True), f"{DOMAIN} notify"
            )
            return
        self._notes += wanted
        if self._notes_cancel is None:
            self._notes_cancel = async_call_later(self.hass, NOTIFY_DELAY, self._flush)

    @callback
    def _flush(self, _now: dt.datetime) -> None:
        self._notes_cancel = None
        batch, self._notes = self._notes, []
        if batch:
            self.config_entry.async_create_background_task(
                self.hass, self._send(batch), f"{DOMAIN} notify"
            )

    async def _send(self, batch: list[dict[str, Any]], missed: bool = False) -> None:
        note = notes.compose(batch, missed=missed)
        opts = self.config_entry.options
        for target in opts.get(CONF_NOTIFY_TARGETS, []):
            try:
                async with asyncio.timeout(NOTIFY_TIMEOUT):
                    await self.hass.services.async_call(
                        "notify",
                        target,
                        notes.service_data(target, note),
                        blocking=True,
                    )
            except (HomeAssistantError, ValueError, TimeoutError) as err:
                if target not in self._failed_targets:
                    _LOGGER.warning(
                        "Could not notify through notify.%s: %s", target, err
                    )
                    self._failed_targets.add(target)
            else:
                self._failed_targets.discard(target)
        if opts.get(CONF_PERSISTENT):
            # One card per item, or per batch, so a later batch does not replace an unread one.
            which = batch[0]["key"] if len(batch) == 1 else model.iso(dt_util.utcnow())
            persistent_notification.async_create(
                self.hass,
                note["markdown"],
                title=note["title"],
                notification_id=f"{DOMAIN}_{self.config_entry.entry_id}_{which}",
            )

    # ------------------------------------------------------------- close

    @override
    async def async_shutdown(self) -> None:
        self._closed = True
        for cancel in self._refresh_cancel.values():
            cancel()
        self._refresh_cancel.clear()
        for handle in (self._notes_cancel, self._sync_cancel):
            if handle:
                handle()
        self._notes_cancel = self._sync_cancel = None
        await super().async_shutdown()


async def _ready(value: dict[str, Any]) -> dict[str, Any]:
    return value


def item_detail(item: Item) -> dict[str, Any]:
    """An item with its body and timeline, for the panel."""
    return {
        **{k: v for k, v in item.items() if k not in INTERNAL},
        "timeline": [
            {k: v for k, v in e.items() if k not in INTERNAL}
            for e in item.get("timeline", [])
        ],
    }
