# Changelog

Everything a user of this integration would notice, newest first. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the version numbers are the ones in `manifest.json`.

## [0.1.1] - 2026-10-23

### Fixed

- Setup started from the Home Assistant app opens GitHub's create page in the phone's browser, at another of Home Assistant's addresses, where GitHub has your sign-in. GitHub had answered "We didn't find an App Manifest for your request".

## [0.1.0] - 2026-10-23

### Added

- A private GitHub App created from the config flow through GitHub's manifest flow, installed on all repositories or a selection, with its webhook on Home Assistant Cloud or the external URL.
- The GitHub Inbox sidebar panel: tiles, charts by repository, age and day, the open-item list with filters, and each item's timeline collapsed to one line per comment or review.
- Sensors for open issues, open pull requests, unseen items and the last delivery; an activity event entity.
- Notifications to chosen notify services and a persistent notification, batched over 5 seconds, with a link into the panel on the Companion app.
- The `github_inbox.resync` administrator action.
- The Your GitHub logins option, for an organization's inbox: the logins counted as you for own activity, unseen and waiting.
- Re-authentication, reconfiguration of the webhook route, a repair when the route is down, and diagnostics.
