"""The GitHub client: JWTs, installation tokens, pagination and how each answer maps to an error."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import patch

import aiohttp
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.github_inbox import api
from custom_components.github_inbox.api import (
    GitHubApp,
    GitHubAuthError,
    GitHubConnectionError,
    GitHubError,
    GitHubForbiddenError,
    GitHubNotFoundError,
    InstallationClient,
    Listing,
)
from tests.ha.conftest import API, APP_ID, INSTALLATION_ID
from tests.payloads import REPO

TOKEN_PATH = f"{API}/app/installations/{INSTALLATION_ID}/access_tokens"


async def test_actions_runs_use_default_branch_and_token_permissions(
    session: aiohttp.ClientSession, aioclient_mock: AiohttpClientMocker, pem: str
) -> None:
    aioclient_mock.post(
        TOKEN_PATH,
        status=201,
        json={
            "token": "ghs_a",
            "expires_at": "2099-01-01T00:00:00Z",
            "permissions": {"actions": "read", "metadata": "read"},
        },
    )
    path = f"{API}/repos/{REPO}/actions/runs"
    aioclient_mock.get(path, json={"workflow_runs": [{"id": 3}]})
    c = client(session, pem)
    assert await c.workflow_runs(REPO, "main") == [{"id": 3}]
    assert c.permissions == {"actions": "read", "metadata": "read"}
    assert aioclient_mock.mock_calls[-1][1].query == {
        "branch": "main",
        "per_page": "50",
    }


@pytest.fixture
async def session(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> AsyncGenerator[aiohttp.ClientSession]:
    s = aioclient_mock.create_session(hass.loop)
    yield s
    await s.close()


def client(session: aiohttp.ClientSession, pem: str) -> InstallationClient:
    return InstallationClient(GitHubApp(session, APP_ID, pem), INSTALLATION_ID)


def mint(
    aioclient_mock: AiohttpClientMocker,
    token: str = "ghs_a",
    expires: str = "2099-01-01T00:00:00Z",
) -> None:
    aioclient_mock.post(
        TOKEN_PATH, status=201, json={"token": token, "expires_at": expires}
    )


async def test_jwt_is_signed_by_the_app_key(
    session: aiohttp.ClientSession, pem: str
) -> None:
    app = GitHubApp(session, APP_ID, pem)
    token = app.jwt(now=1_000_000)
    public = serialization.load_pem_private_key(pem.encode(), None).public_key()
    claims = jwt.decode(
        token,
        public,
        algorithms=["RS256"],
        options={"verify_exp": False, "verify_iat": False},
    )
    assert claims == {"iat": 999_940, "exp": 1_000_540, "iss": str(APP_ID)}
    assert app.jwt() != token


async def test_a_key_that_does_not_load_is_an_auth_error(
    session: aiohttp.ClientSession,
) -> None:
    with pytest.raises(GitHubAuthError):
        GitHubApp(session, APP_ID, "not a key").jwt()
    ec_pem = (
        ec.generate_private_key(ec.SECP256R1())
        .private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        .decode()
    )
    with pytest.raises(GitHubAuthError, match="not an RSA key"):
        GitHubApp(session, APP_ID, ec_pem).jwt()


async def test_token_is_minted_once_and_renewed_near_expiry(
    session: aiohttp.ClientSession, aioclient_mock: AiohttpClientMocker, pem: str
) -> None:
    soon = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3600))
    mint(aioclient_mock, expires=soon)
    c = client(session, pem)
    assert await c.token() == "ghs_a"
    assert await c.token() == "ghs_a"
    assert aioclient_mock.call_count == 1
    with patch(
        "custom_components.github_inbox.api.time.time", return_value=time.time() + 3400
    ):
        await c.token()
    assert aioclient_mock.call_count == 2
    headers = aioclient_mock.mock_calls[0][3]
    assert headers["Authorization"].startswith("Bearer ey")
    assert headers["X-GitHub-Api-Version"] == api.API_VERSION


async def test_a_revoked_token_is_replaced_once(
    session: aiohttp.ClientSession, aioclient_mock: AiohttpClientMocker, pem: str
) -> None:
    mint(aioclient_mock)
    answers = iter([401, 200])

    async def repos(method: str, url: Any, data: Any) -> Any:
        status = next(answers)
        return aioclient_mock_response(
            status, {"repositories": [{"full_name": REPO}]} if status == 200 else {}
        )

    aioclient_mock.get(f"{API}/installation/repositories", side_effect=repos)
    assert await client(session, pem).repositories() == Listing(
        [{"full_name": REPO}], complete=True
    )
    assert (
        sum(1 for c in aioclient_mock.mock_calls if c[1].path.endswith("access_tokens"))
        == 2
    )


def aioclient_mock_response(
    status: int, payload: Any, headers: dict[str, str] | None = None
) -> Any:
    from pytest_homeassistant_custom_component.test_util.aiohttp import (
        AiohttpClientMockResponse,
    )
    from yarl import URL

    return AiohttpClientMockResponse(
        "get", URL(API), status=status, json=payload, headers=headers
    )


async def test_pages_follow_the_link_header_on_the_api_host_only(
    session: aiohttp.ClientSession, aioclient_mock: AiohttpClientMocker, pem: str
) -> None:
    mint(aioclient_mock)
    url = f"{API}/repos/{REPO}/issues"
    aioclient_mock.get(
        f"{url}?page=2",
        json=[{"number": 2}],
        headers={"Link": '<https://evil.example/x?page=3>; rel="next"'},
    )
    aioclient_mock.get(
        url,
        json=[{"number": 1}, "junk"],
        headers={"Link": f'<{url}?page=1>; rel="prev", <{url}?page=2>; rel="next"'},
    )
    listing = await client(session, pem).open_issues(REPO)
    assert [r["number"] for r in listing.rows] == [1, 2]
    assert listing.complete
    first = aioclient_mock.mock_calls[1]
    assert first[1].query["state"] == "open"
    assert first[1].query["per_page"] == "100"
    assert first[3]["Accept"] == api.ACCEPT_FULL


async def test_pages_stop_at_the_cap(
    session: aiohttp.ClientSession, aioclient_mock: AiohttpClientMocker, pem: str
) -> None:
    mint(aioclient_mock)
    url = f"{API}/repos/{REPO}/issues/1/comments"
    aioclient_mock.get(
        url, json=[{"id": 1}], headers={"Link": f'<{url}?page=2>; rel="next"'}
    )
    rows = await client(session, pem).issue_comments(REPO, 1)
    assert len(rows) == api.MAX_PAGES
    aioclient_mock.get(
        f"{API}/installation/repositories",
        json={"repositories": [{"full_name": REPO}]},
        headers={"Link": f'<{API}/installation/repositories?page=2>; rel="next"'},
    )
    listing = await client(session, pem).repositories()
    assert len(listing.rows) == api.MAX_PAGES
    assert not listing.complete


async def test_a_list_answer_without_its_key_is_an_error(
    session: aiohttp.ClientSession, aioclient_mock: AiohttpClientMocker, pem: str
) -> None:
    mint(aioclient_mock)
    aioclient_mock.get(f"{API}/installation/repositories", json={"total_count": 0})
    with pytest.raises(GitHubConnectionError, match="has no repositories"):
        await client(session, pem).repositories()


async def test_a_list_answer_that_is_not_a_list_is_an_error(
    session: aiohttp.ClientSession, aioclient_mock: AiohttpClientMocker, pem: str
) -> None:
    mint(aioclient_mock)
    aioclient_mock.get(f"{API}/repos/{REPO}/pulls/1/reviews", json={"message": "odd"})
    with pytest.raises(GitHubConnectionError, match="not a list"):
        await client(session, pem).reviews(REPO, 1)


async def test_a_secondary_rate_limit_is_named_in_the_message(
    session: aiohttp.ClientSession, aioclient_mock: AiohttpClientMocker, pem: str
) -> None:
    aioclient_mock.get(
        f"{API}/app",
        status=403,
        json={"message": "You have exceeded a secondary rate limit."},
    )
    with pytest.raises(GitHubConnectionError, match="rate limited"):
        await GitHubApp(session, APP_ID, pem).get_app()


@pytest.mark.parametrize(
    ("status", "headers", "error"),
    [
        (401, {}, GitHubAuthError),
        (403, {"x-ratelimit-remaining": "0"}, GitHubConnectionError),
        (403, {"retry-after": "60"}, GitHubConnectionError),
        (429, {}, GitHubConnectionError),
        (403, {}, GitHubForbiddenError),
        (404, {}, GitHubNotFoundError),
        (410, {}, GitHubNotFoundError),
        (500, {}, GitHubConnectionError),
        (422, {}, GitHubError),
    ],
)
async def test_status_maps_to_an_error(
    session: aiohttp.ClientSession,
    aioclient_mock: AiohttpClientMocker,
    pem: str,
    status: int,
    headers: dict[str, str],
    error: type[Exception],
) -> None:
    aioclient_mock.get(f"{API}/app", status=status, headers=headers, json={})
    with pytest.raises(error) as caught:
        await GitHubApp(session, APP_ID, pem).get_app()
    assert type(caught.value) is error
    assert "/app" in str(caught.value)


@pytest.mark.parametrize("exc", [aiohttp.ClientError(), TimeoutError()])
async def test_transport_failures_are_connection_errors(
    session: aiohttp.ClientSession,
    aioclient_mock: AiohttpClientMocker,
    pem: str,
    exc: Exception,
) -> None:
    aioclient_mock.get(f"{API}/app", exc=exc)
    with pytest.raises(GitHubConnectionError):
        await GitHubApp(session, APP_ID, pem).get_app()


async def test_an_unreadable_body_is_a_connection_error(
    session: aiohttp.ClientSession, aioclient_mock: AiohttpClientMocker, pem: str
) -> None:
    aioclient_mock.get(f"{API}/app", text="not json")
    with pytest.raises(GitHubConnectionError):
        await GitHubApp(session, APP_ID, pem).get_app()


async def test_token_errors(
    session: aiohttp.ClientSession, aioclient_mock: AiohttpClientMocker, pem: str
) -> None:
    aioclient_mock.post(TOKEN_PATH, status=404, json={})
    with pytest.raises(GitHubAuthError):
        await client(session, pem).token()
    aioclient_mock.clear_requests()
    aioclient_mock.post(
        TOKEN_PATH, status=403, json={"message": "This installation has been suspended"}
    )
    with pytest.raises(GitHubAuthError):
        await client(session, pem).token()
    aioclient_mock.clear_requests()
    aioclient_mock.post(TOKEN_PATH, status=201, json={"nothing": True})
    with pytest.raises(GitHubConnectionError):
        await client(session, pem).token()


async def test_an_unreadable_expiry_mints_a_token_per_call(
    session: aiohttp.ClientSession, aioclient_mock: AiohttpClientMocker, pem: str
) -> None:
    mint(aioclient_mock, expires="garbage")
    c = client(session, pem)
    assert await c.token() == "ghs_a"
    assert await c.token() == "ghs_a"
    assert aioclient_mock.call_count == 2


async def test_concurrent_reads_mint_one_token(
    session: aiohttp.ClientSession, aioclient_mock: AiohttpClientMocker, pem: str
) -> None:
    mint(aioclient_mock)
    c = client(session, pem)
    assert await asyncio.gather(*(c.token() for _ in range(6))) == ["ghs_a"] * 6
    assert aioclient_mock.call_count == 1


async def test_app_calls(
    session: aiohttp.ClientSession, aioclient_mock: AiohttpClientMocker, pem: str
) -> None:
    aioclient_mock.get(
        f"{API}/app/installations/{INSTALLATION_ID}", json={"id": INSTALLATION_ID}
    )
    aioclient_mock.patch(f"{API}/app/hook/config", status=204)
    app = GitHubApp(session, APP_ID, pem)
    assert await app.installation(INSTALLATION_ID) == {"id": INSTALLATION_ID}
    await app.update_hook_url("https://hooks.example/new")
    assert aioclient_mock.mock_calls[-1][2] == {
        "url": "https://hooks.example/new",
        "content_type": "json",
    }


async def test_reads_use_the_repository_paths(
    session: aiohttp.ClientSession, aioclient_mock: AiohttpClientMocker, pem: str
) -> None:
    mint(aioclient_mock)
    aioclient_mock.get(f"{API}/repos/{REPO}/issues/3", json={"number": 3})
    aioclient_mock.get(f"{API}/repos/{REPO}/pulls/4", json={"number": 4})
    aioclient_mock.get(f"{API}/repos/{REPO}/pulls/4/comments", json=[{"id": 9}])
    c = client(session, pem)
    assert (await c.issue(REPO, 3))["number"] == 3
    assert (await c.pull(REPO, 4))["number"] == 4
    assert await c.review_comments(REPO, 4) == [{"id": 9}]


@pytest.mark.parametrize("bad", ["../x", "a/b/c", "a", "a/..", "./b", "a b/c"])
def test_repository_names_are_checked(bad: str) -> None:
    with pytest.raises(GitHubNotFoundError):
        api.repo_path(bad)
    assert api.repo_path("a/b..c") == "/repos/a/b..c"


def test_next_link() -> None:
    assert api.next_link("") is None
    assert (
        api.next_link('<https://api.github.com/x?page=2>; rel="next"')
        == "https://api.github.com/x?page=2"
    )
    assert api.next_link('<https://api.github.com/x?page=1>; rel="prev"') is None


async def test_manifest_conversion(
    session: aiohttp.ClientSession, aioclient_mock: AiohttpClientMocker, pem: str
) -> None:
    url = f"{API}/app-manifests/code%2F1/conversions"
    aioclient_mock.post(
        url,
        status=201,
        json={
            "id": APP_ID,
            "slug": "ha-inbox",
            "html_url": "https://github.com/apps/ha-inbox",
            "pem": pem,
            "webhook_secret": "s",
            "owner": {"login": "heidrickla", "id": 5},
        },
    )
    creds = await api.convert_manifest(session, "code/1")
    assert (creds.app_id, creds.slug, creds.owner_login, creds.owner_id) == (
        APP_ID,
        "ha-inbox",
        "heidrickla",
        5,
    )
    assert "Authorization" not in aioclient_mock.mock_calls[0][3]
    aioclient_mock.clear_requests()
    aioclient_mock.post(url, status=201, json={"id": APP_ID})
    with pytest.raises(GitHubError, match="without the app's credentials"):
        await api.convert_manifest(session, "code/1")
