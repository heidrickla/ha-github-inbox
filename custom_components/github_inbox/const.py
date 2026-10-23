"""Constants for GitHub Inbox."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "github_inbox"
VERSION: Final = "0.1.0"
NAME: Final = "GitHub Inbox"
DOCS_URL: Final = "https://github.com/heidrickla/ha-github-inbox"

# Config entry data. The private key and webhook secret come from GitHub's
# manifest conversion and are never shown again.
CONF_APP_ID: Final = "app_id"
CONF_APP_SLUG: Final = "app_slug"
CONF_APP_URL: Final = "app_url"
CONF_PRIVATE_KEY: Final = "private_key"
CONF_WEBHOOK_SECRET: Final = "webhook_secret"
CONF_WEBHOOK_ID: Final = "webhook_id"
CONF_CLOUDHOOK: Final = "cloudhook"
CONF_INSTALLATION_ID: Final = "installation_id"
CONF_ACCOUNT: Final = "account"
CONF_ACCOUNT_ID: Final = "account_id"

# Config flow form fields.
CONF_APP_NAME: Final = "app_name"
CONF_ORGANIZATION: Final = "organization"
CONF_ROUTE: Final = "route"
ROUTE_CLOUD: Final = "cloud"
ROUTE_EXTERNAL: Final = "external"

# Options.
CONF_NOTIFY_TARGETS: Final = "notify_targets"
CONF_PERSISTENT: Final = "persistent_notification"
CONF_NOTIFY_ON: Final = "notify_on"
CONF_INCLUDE_BOTS: Final = "include_bots"
CONF_INCLUDE_OWN: Final = "include_own"
CONF_ME: Final = "me"

NOTIFY_OPENED: Final = "opened"
NOTIFY_COMMENTS: Final = "comments"
NOTIFY_REVIEWS: Final = "reviews"
NOTIFY_CLOSED: Final = "closed"
NOTIFY_CHOICES: Final = (NOTIFY_OPENED, NOTIFY_COMMENTS, NOTIFY_REVIEWS, NOTIFY_CLOSED)
DEFAULT_NOTIFY_ON: Final = [NOTIFY_OPENED, NOTIFY_COMMENTS, NOTIFY_REVIEWS]

SERVICE_RESYNC: Final = "resync"

ISSUE_WEBHOOK_ROUTE: Final = "webhook_route"

# The events the GitHub App subscribes to, and the permissions they need.
APP_EVENTS: Final = (
    "issues",
    "issue_comment",
    "pull_request",
    "pull_request_review",
    "pull_request_review_comment",
    "repository",
)
APP_PERMISSIONS: Final = {
    "issues": "read",
    "pull_requests": "read",
    "metadata": "read",
}

# Activity types, shared by the activity log, the event entity and the panel.
ACTIVITY_OPENED: Final = "opened"
ACTIVITY_REOPENED: Final = "reopened"
ACTIVITY_CLOSED: Final = "closed"
ACTIVITY_MERGED: Final = "merged"
ACTIVITY_COMMENTED: Final = "commented"
ACTIVITY_REVIEWED: Final = "reviewed"
ACTIVITY_TYPES: Final = (
    ACTIVITY_OPENED,
    ACTIVITY_REOPENED,
    ACTIVITY_CLOSED,
    ACTIVITY_MERGED,
    ACTIVITY_COMMENTED,
    ACTIVITY_REVIEWED,
)

PANEL_URL: Final = "github-inbox"
PANEL_ELEMENT: Final = "github-inbox-panel"
PANEL_ICON: Final = "mdi:github"
STATIC_URL: Final = "/github_inbox_static"

SIGNAL_UPDATED: Final = f"{DOMAIN}_updated"

# Seconds an item waits after its last event before it is fetched again, so a
# pull request opened with labels and reviewers is one fetch.
REFRESH_DELAY: Final = 2.0
# Seconds notifications are held so a burst becomes one message.
NOTIFY_DELAY: Final = 5.0
# Retry schedule after a failed sync, in seconds.
SYNC_RETRY: Final = (300, 900, 3600)

ACTIVITY_KEEP: Final = 500
ACTIVITY_DAYS: Final = 30
DELIVERY_KEEP: Final = 500
TIMELINE_KEEP: Final = 200
