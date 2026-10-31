"""Fixtures for the Home Assistant layer tests.

GitHub is answered by aioclient_mock. Home Assistant Cloud is replaced at the route module, because importing the cloud
integration loads Assist and its requirements; tests/ha/test_route.py covers the route module against a stand-in.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Generator, Iterable, Mapping
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.github_inbox.const import (
    CONF_ACCOUNT,
    CONF_ACCOUNT_ID,
    CONF_APP_ID,
    CONF_APP_SLUG,
    CONF_APP_URL,
    CONF_CLOUDHOOK,
    CONF_INCLUDE_BOTS,
    CONF_INCLUDE_OWN,
    CONF_INSTALLATION_ID,
    CONF_NOTIFY_ON,
    CONF_NOTIFY_TARGETS,
    CONF_PERSISTENT,
    CONF_PRIVATE_KEY,
    CONF_WEBHOOK_ID,
    CONF_WEBHOOK_SECRET,
    DEFAULT_NOTIFY_ON,
    DOMAIN,
)
from custom_components.github_inbox.coordinator import InboxCoordinator
from tests.payloads import REPO

API = "https://api.github.com"
APP_ID = 4242
APP_URL = "https://github.com/apps/ha-inbox-test"
INSTALLATION_ID = 777
ACCOUNT = "heidrickla"
ACCOUNT_ID = 1234
WEBHOOK_ID = "inbox-webhook-id"
SECRET = "webhook-secret-for-tests"
HOOK_URL = "https://hooks.nabu.casa/test-hook"


@pytest.fixture(scope="session")
def pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: Any) -> None:
    return


@pytest.fixture(autouse=True)
def mock_route() -> Generator[dict[str, MagicMock]]:
    """Home Assistant Cloud signed in and connected, with a cloudhook."""
    base = "custom_components.github_inbox.route"
    with (
        patch(f"{base}.cloud_ready", return_value=True) as ready,
        patch(f"{base}.cloud_signed_in", return_value=True) as signed_in,
        patch(f"{base}.external_url", return_value=None) as external,
        patch(f"{base}.async_hook_url", AsyncMock(return_value=HOOK_URL)) as hook,
        patch(f"{base}.async_delete_cloudhook", AsyncMock()) as delete,
        patch(f"{base}.listen_cloud", return_value=MagicMock()) as listen,
    ):
        yield {
            "ready": ready,
            "signed_in": signed_in,
            "external": external,
            "hook": hook,
            "delete": delete,
            "listen": listen,
        }


def entry_data(pem: str, **extra: Any) -> dict[str, Any]:
    return {
        CONF_APP_ID: APP_ID,
        CONF_APP_SLUG: "ha-inbox-test",
        CONF_APP_URL: APP_URL,
        CONF_PRIVATE_KEY: pem,
        CONF_WEBHOOK_SECRET: SECRET,
        CONF_WEBHOOK_ID: WEBHOOK_ID,
        CONF_CLOUDHOOK: True,
        CONF_INSTALLATION_ID: INSTALLATION_ID,
        CONF_ACCOUNT: ACCOUNT,
        CONF_ACCOUNT_ID: ACCOUNT_ID,
        **extra,
    }


OPTIONS = {
    CONF_NOTIFY_TARGETS: [],
    CONF_PERSISTENT: False,
    CONF_NOTIFY_ON: list(DEFAULT_NOTIFY_ON),
    CONF_INCLUDE_BOTS: False,
    CONF_INCLUDE_OWN: False,
}


@pytest.fixture
def config_entry(pem: str) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title=ACCOUNT,
        unique_id=str(ACCOUNT_ID),
        data=entry_data(pem),
        options=dict(OPTIONS),
    )


class GitHub:
    """Registers GitHub's answers on aioclient_mock."""

    def __init__(self, mocker: AiohttpClientMocker) -> None:
        self.m = mocker

    def reset(self) -> None:
        self.m.clear_requests()

    def token(
        self, status: int = 201, permissions: dict[str, str] | None = None
    ) -> None:
        self.m.post(
            f"{API}/app/installations/{INSTALLATION_ID}/access_tokens",
            status=status,
            json={
                "token": "ghs_test",
                "expires_at": "2099-01-01T00:00:00Z",
                "permissions": permissions
                if permissions is not None
                else {"actions": "read"},
            },
        )

    def repos(
        self,
        names: Iterable[str] = (REPO,),
        status: int = 200,
        archived: Iterable[str] = (),
        runs_by_repo: Mapping[str, list[dict[str, Any]]] | None = None,
    ) -> None:
        rows: list[dict[str, Any]] = [
            {"full_name": n, "archived": False, "default_branch": "main"} for n in names
        ]
        rows += [{"full_name": n, "archived": True} for n in archived]
        rows.append({"archived": False})
        self.m.get(
            f"{API}/installation/repositories",
            status=status,
            json={"total_count": len(rows), "repositories": rows},
        )
        for row in rows:
            if row.get("full_name") and not row.get("archived"):
                self.runs(
                    (runs_by_repo or {}).get(row["full_name"], []),
                    repo=row["full_name"],
                )

    def runs(
        self, rows: list[dict[str, Any]], repo: str = REPO, status: int = 200
    ) -> None:
        self.m.get(
            f"{API}/repos/{repo}/actions/runs",
            status=status,
            json={"workflow_runs": rows},
        )

    def issues(
        self, rows: list[dict[str, Any]], repo: str = REPO, status: int = 200
    ) -> None:
        self.m.get(f"{API}/repos/{repo}/issues", status=status, json=rows)

    def issue(
        self, number: int, obj: dict[str, Any], repo: str = REPO, status: int = 200
    ) -> None:
        self.m.get(f"{API}/repos/{repo}/issues/{number}", status=status, json=obj)

    def comments(
        self, number: int, rows: list[dict[str, Any]], repo: str = REPO
    ) -> None:
        self.m.get(f"{API}/repos/{repo}/issues/{number}/comments", json=rows)

    def pull(
        self,
        number: int,
        obj: dict[str, Any],
        *,
        comments: list[dict[str, Any]] | None = None,
        reviews: list[dict[str, Any]] | None = None,
        review_comments: list[dict[str, Any]] | None = None,
        repo: str = REPO,
        status: int = 200,
    ) -> None:
        self.m.get(f"{API}/repos/{repo}/pulls/{number}", status=status, json=obj)
        self.comments(number, comments or [], repo)
        self.m.get(f"{API}/repos/{repo}/pulls/{number}/reviews", json=reviews or [])
        self.m.get(
            f"{API}/repos/{repo}/pulls/{number}/comments", json=review_comments or []
        )

    def calls(self, method: str, path: str) -> int:
        return sum(
            1
            for m, url, _data, _headers in self.m.mock_calls
            if m.lower() == method.lower() and url.path == path
        )


@pytest.fixture
def github(aioclient_mock: AiohttpClientMocker) -> GitHub:
    return GitHub(aioclient_mock)


async def setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> InboxCoordinator:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    coordinator: InboxCoordinator = entry.runtime_data
    return coordinator


def signed(payload: dict[str, Any], secret: str = SECRET) -> tuple[bytes, str]:
    body = json.dumps(payload).encode()
    return body, "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


async def deliver(
    client: Any,
    event: str,
    payload: dict[str, Any],
    guid: str = "delivery-1",
    secret: str = SECRET,
) -> Any:
    body, signature = signed(payload, secret)
    return await client.post(
        f"/api/webhook/{WEBHOOK_ID}",
        data=body,
        headers={
            "X-Hub-Signature-256": signature,
            "X-GitHub-Event": event,
            "X-GitHub-Delivery": guid,
            "Content-Type": "application/json",
        },
    )
