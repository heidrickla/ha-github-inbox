"""GitHub REST client for a GitHub App: app JWTs, installation tokens and the reads the inbox needs.

No Home Assistant imports, so the client is tested on its own.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import aiohttp
import jwt
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key

API = "https://api.github.com"
ACCEPT_JSON = "application/vnd.github+json"
# full+json carries the Markdown source, its text and GitHub's rendered HTML.
ACCEPT_FULL = "application/vnd.github.full+json"
API_VERSION = "2022-11-28"
USER_AGENT = "ha-github-inbox"
TIMEOUT = aiohttp.ClientTimeout(total=30)
MAX_PAGES = 10
# An installation token lives an hour; a new one is minted this many seconds
# before the old one expires.
TOKEN_MARGIN = 300

_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class GitHubError(Exception):
    """A request to GitHub failed."""


class GitHubConnectionError(GitHubError):
    """GitHub could not be reached, answered with a server error or rate-limited the call."""


class GitHubAuthError(GitHubError):
    """GitHub refused the app's key, or the installation is gone or suspended."""


class GitHubNotFoundError(GitHubError):
    """The resource is gone: deleted, disabled or blocked for legal reasons."""


class GitHubForbiddenError(GitHubError):
    """GitHub refused the read (a permission, a block or an IP allow list); the resource may still exist."""


@dataclass(frozen=True, slots=True)
class Listing:
    """The rows of a paginated list, and whether every page was read."""

    rows: list[dict[str, Any]]
    complete: bool


@dataclass(frozen=True, slots=True)
class AppCredentials:
    """What GitHub hands over once, when a manifest becomes an app."""

    app_id: int
    slug: str
    html_url: str
    pem: str
    webhook_secret: str
    owner_login: str
    owner_id: int


def _headers(token: str | None, accept: str = ACCEPT_JSON) -> dict[str, str]:
    headers = {
        "Accept": accept,
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": API_VERSION,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def repo_path(repo: str) -> str:
    """`/repos/<owner>/<name>` for a full name, refusing anything else."""
    if not _REPO_RE.match(repo) or {".", ".."} & set(repo.split("/")):
        raise GitHubNotFoundError(f"not a repository name: {repo!r}")
    return f"/repos/{repo}"


_NEXT_RE = re.compile(r'<([^>]+)>\s*;\s*rel="?next"?')


def next_link(header: str) -> str | None:
    """The `rel="next"` URL of a Link header."""
    match = _NEXT_RE.search(header or "")
    return match.group(1) if match else None


async def _raise_for(resp: aiohttp.ClientResponse, path: str) -> None:
    status = resp.status
    if status == 401:
        raise GitHubAuthError(f"{path}: HTTP 401")
    if status in (403, 429):
        # A secondary rate limit can answer 403 with neither header; its message names it.
        message = (await resp.text(errors="replace"))[:2000].lower()
        if (
            status == 429
            or resp.headers.get("x-ratelimit-remaining") == "0"
            or "retry-after" in resp.headers
            or "rate limit" in message
        ):
            raise GitHubConnectionError(f"{path}: rate limited")
        raise GitHubForbiddenError(f"{path}: HTTP 403")
    if status in (404, 410, 451):
        raise GitHubNotFoundError(f"{path}: HTTP {status}")
    if status >= 500:
        raise GitHubConnectionError(f"{path}: HTTP {status}")
    raise GitHubError(f"{path}: HTTP {status}")


async def _send(
    session: aiohttp.ClientSession,
    method: str,
    path: str,
    headers: dict[str, str],
    *,
    params: dict[str, str] | None = None,
    json_body: dict[str, Any] | None = None,
) -> tuple[Any, str | None]:
    """One request. Returns the decoded body and the next page's URL, if any."""
    url = path if path.startswith(API + "/") else API + path
    shown = url.removeprefix(API).split("?")[0]
    try:
        async with session.request(
            method,
            url,
            headers=headers,
            params=params,
            json=json_body,
            timeout=TIMEOUT,
        ) as resp:
            if resp.status >= 400:
                await _raise_for(resp, shown)
            if resp.status == 204:
                return None, None
            data = await resp.json(content_type=None)
            next_url = next_link(resp.headers.get("Link", ""))
    except (aiohttp.ClientError, TimeoutError, ValueError) as err:
        raise GitHubConnectionError(f"{shown}: {type(err).__name__}") from err
    # A next link is followed only on the API host, so a token never goes elsewhere.
    if next_url and not next_url.startswith(API + "/"):
        next_url = None
    return data, next_url


async def convert_manifest(session: aiohttp.ClientSession, code: str) -> AppCredentials:
    """Exchange the code GitHub returned for the new app's credentials."""
    data, _ = await _send(
        session,
        "POST",
        f"/app-manifests/{quote(code, safe='')}/conversions",
        _headers(None),
    )
    try:
        return AppCredentials(
            app_id=int(data["id"]),
            slug=str(data["slug"]),
            html_url=str(data["html_url"]),
            pem=str(data["pem"]),
            webhook_secret=str(data["webhook_secret"]),
            owner_login=str(data["owner"]["login"]),
            owner_id=int(data["owner"]["id"]),
        )
    except (KeyError, TypeError, ValueError) as err:
        raise GitHubError(
            "the conversion answered without the app's credentials"
        ) from err


def _parse_time(value: Any) -> float:
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


class GitHubApp:
    """Calls made as the app itself, signed with its private key."""

    def __init__(self, session: aiohttp.ClientSession, app_id: int, pem: str) -> None:
        self.session = session
        self.app_id = app_id
        self._pem = pem
        self._key: RSAPrivateKey | None = None

    def _private_key(self) -> RSAPrivateKey:
        if self._key is None:
            try:
                key = load_pem_private_key(self._pem.encode(), password=None)
            except (ValueError, TypeError) as err:
                raise GitHubAuthError("the stored private key does not load") from err
            if not isinstance(key, RSAPrivateKey):
                raise GitHubAuthError("the stored private key is not an RSA key")
            self._key = key
        return self._key

    def jwt(self, now: float | None = None) -> str:
        """A JWT GitHub accepts for ten minutes; issued a minute early for clock skew."""
        issued = int(now if now is not None else time.time()) - 60
        return jwt.encode(
            {"iat": issued, "exp": issued + 600, "iss": str(self.app_id)},
            self._private_key(),
            algorithm="RS256",
        )

    async def _call(
        self, method: str, path: str, json_body: dict[str, Any] | None = None
    ) -> Any:
        data, _ = await _send(
            self.session, method, path, _headers(self.jwt()), json_body=json_body
        )
        return data

    async def get_app(self) -> dict[str, Any]:
        data: dict[str, Any] = await self._call("GET", "/app")
        return data

    async def installation(self, installation_id: int) -> dict[str, Any]:
        data: dict[str, Any] = await self._call(
            "GET", f"/app/installations/{int(installation_id)}"
        )
        return data

    async def update_hook_url(self, url: str) -> None:
        await self._call(
            "PATCH", "/app/hook/config", {"url": url, "content_type": "json"}
        )

    async def create_token(self, installation_id: int) -> tuple[str, float]:
        """An installation token and when it expires (epoch seconds)."""
        try:
            data = await self._call(
                "POST", f"/app/installations/{int(installation_id)}/access_tokens"
            )
        except (GitHubNotFoundError, GitHubForbiddenError) as err:
            # 404 is an installation that is gone, 403 one that is suspended.
            raise GitHubAuthError(str(err)) from err
        try:
            return str(data["token"]), _parse_time(data["expires_at"])
        except (KeyError, TypeError) as err:
            raise GitHubConnectionError("the token answer had no token") from err


class InstallationClient:
    """Reads made as the installation, with a token minted on demand."""

    def __init__(self, app: GitHubApp, installation_id: int) -> None:
        self.app = app
        self.installation_id = installation_id
        self._token: str | None = None
        self._expires = 0.0
        self._lock = asyncio.Lock()

    async def token(self) -> str:
        async with self._lock:
            if self._token is None or self._expires - TOKEN_MARGIN <= time.time():
                self._token, self._expires = await self.app.create_token(
                    self.installation_id
                )
            return self._token

    async def _get(
        self, path: str, params: dict[str, str] | None, accept: str
    ) -> tuple[Any, str | None]:
        token = await self.token()
        try:
            return await _send(
                self.app.session, "GET", path, _headers(token, accept), params=params
            )
        except GitHubAuthError:
            # A token can be revoked before it expires; one new token is tried.
            async with self._lock:
                if self._token == token:
                    self._token = None
            token = await self.token()
            return await _send(
                self.app.session, "GET", path, _headers(token, accept), params=params
            )

    async def get(self, path: str, *, html: bool = False) -> Any:
        data, _ = await self._get(path, None, ACCEPT_FULL if html else ACCEPT_JSON)
        return data

    async def listing(
        self,
        path: str,
        *,
        params: dict[str, str] | None = None,
        html: bool = False,
        key: str | None = None,
    ) -> Listing:
        """Every page of a list, up to MAX_PAGES of 100, and whether that was all of it."""
        accept = ACCEPT_FULL if html else ACCEPT_JSON
        out: list[dict[str, Any]] = []
        url: str | None = path
        query: dict[str, str] | None = {"per_page": "100", **(params or {})}
        for _ in range(MAX_PAGES):
            if url is None:
                break
            data, url = await self._get(url, query, accept)
            # The next link carries the query itself.
            query = None
            if key is not None:
                if not isinstance(data, dict) or key not in data:
                    raise GitHubConnectionError(f"{path}: the answer has no {key}")
                data = data[key]
            if not isinstance(data, list):
                raise GitHubConnectionError(f"{path}: the answer is not a list")
            out.extend(row for row in data if isinstance(row, dict))
        return Listing(out, complete=url is None)

    async def get_all(
        self, path: str, *, html: bool = False, key: str | None = None
    ) -> list[dict[str, Any]]:
        """The rows of a list, up to MAX_PAGES of 100."""
        return (await self.listing(path, html=html, key=key)).rows

    async def repositories(self) -> Listing:
        return await self.listing("/installation/repositories", key="repositories")

    async def open_issues(self, repo: str) -> Listing:
        """Open issues and pull requests; GitHub lists both as issues."""
        return await self.listing(
            f"{repo_path(repo)}/issues", params={"state": "open"}, html=True
        )

    async def issue(self, repo: str, number: int) -> dict[str, Any]:
        data: dict[str, Any] = await self.get(
            f"{repo_path(repo)}/issues/{int(number)}", html=True
        )
        return data

    async def issue_comments(self, repo: str, number: int) -> list[dict[str, Any]]:
        return await self.get_all(
            f"{repo_path(repo)}/issues/{int(number)}/comments", html=True
        )

    async def pull(self, repo: str, number: int) -> dict[str, Any]:
        data: dict[str, Any] = await self.get(
            f"{repo_path(repo)}/pulls/{int(number)}", html=True
        )
        return data

    async def reviews(self, repo: str, number: int) -> list[dict[str, Any]]:
        return await self.get_all(
            f"{repo_path(repo)}/pulls/{int(number)}/reviews", html=True
        )

    async def review_comments(self, repo: str, number: int) -> list[dict[str, Any]]:
        return await self.get_all(
            f"{repo_path(repo)}/pulls/{int(number)}/comments", html=True
        )
