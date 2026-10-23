"""Config flow: create a private GitHub App from a manifest, install it, and keep its webhook pointed here.

Nothing is typed but a name. GitHub creates the app from the manifest the flow posts (its webhook URL, events and
read-only permissions), hands its key and webhook secret to Home Assistant once, and the account owner installs it on
all repositories or a selection.
"""

from __future__ import annotations

import re
import secrets
from typing import Any, override

import voluptuous as vol
from homeassistant.components import webhook
from homeassistant.config_entries import (
    SOURCE_REAUTH,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.http import current_request
from homeassistant.helpers.network import NoURLAvailableError, get_url
from homeassistant.helpers.selector import (
    BooleanSelector,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
)

from . import model, route, views
from .api import (
    AppCredentials,
    GitHubApp,
    GitHubAuthError,
    GitHubError,
    GitHubNotFoundError,
    InstallationClient,
    convert_manifest,
)
from .const import (
    APP_EVENTS,
    APP_PERMISSIONS,
    CONF_ACCOUNT,
    CONF_ACCOUNT_ID,
    CONF_APP_ID,
    CONF_APP_NAME,
    CONF_APP_SLUG,
    CONF_APP_URL,
    CONF_CLOUDHOOK,
    CONF_INCLUDE_BOTS,
    CONF_INCLUDE_OWN,
    CONF_INSTALLATION_ID,
    CONF_ME,
    CONF_NOTIFY_ON,
    CONF_NOTIFY_TARGETS,
    CONF_ORGANIZATION,
    CONF_PERSISTENT,
    CONF_PRIVATE_KEY,
    CONF_ROUTE,
    CONF_WEBHOOK_ID,
    CONF_WEBHOOK_SECRET,
    DEFAULT_NOTIFY_ON,
    DOCS_URL,
    DOMAIN,
    NOTIFY_CHOICES,
    ROUTE_CLOUD,
    ROUTE_EXTERNAL,
)
from .coordinator import InboxConfigEntry

ORG_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
NAME_MAX = 34
GITHUB = "https://github.com"


def build_manifest(name: str, hook_url: str) -> dict[str, Any]:
    """The app GitHub creates: private, read-only, subscribed to the inbox's events."""
    return {
        "name": name,
        "url": DOCS_URL,
        "description": "Open issues and pull requests in Home Assistant.",
        "public": False,
        "hook_attributes": {"url": hook_url, "active": True},
        "default_permissions": dict(APP_PERMISSIONS),
        "default_events": list(APP_EVENTS),
        "request_oauth_on_install": False,
        "setup_on_update": False,
    }


def _route_selector(routes: list[str]) -> SelectSelector:
    return SelectSelector(
        SelectSelectorConfig(
            options=routes, translation_key="route", mode=SelectSelectorMode.LIST
        )
    )


class GitHubInboxConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._default_name = f"HA Inbox {secrets.token_hex(3)}"
        self._nonce: str | None = None
        self._webhook_id: str | None = None
        self._route = ROUTE_CLOUD
        self._code = ""
        self._creds: AppCredentials | None = None
        self._installation: dict[str, Any] | None = None
        # Set once the webhook belongs to an entry, so closing the flow keeps its cloudhook.
        self._keep_hook = False

    @staticmethod
    @callback
    @override
    def async_get_options_flow(config_entry: InboxConfigEntry) -> InboxOptionsFlow:
        return InboxOptionsFlow()

    # ------------------------------------------------------ app creation

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        routes = route.available_routes(self.hass)
        if not routes:
            return self.async_abort(reason="no_route")
        errors: dict[str, str] = {}
        if user_input is not None:
            name = str(user_input[CONF_APP_NAME]).strip()
            org = str(user_input.get(CONF_ORGANIZATION) or "").strip()
            chosen = str(user_input.get(CONF_ROUTE) or routes[0])
            if not name or len(name) > NAME_MAX:
                errors[CONF_APP_NAME] = "invalid_name"
            elif org and not ORG_RE.match(org):
                errors[CONF_ORGANIZATION] = "invalid_organization"
            elif chosen not in routes:
                errors[CONF_ROUTE] = "route_unavailable"
            else:
                self._webhook_id = self._webhook_id or webhook.async_generate_id()
                try:
                    hook_url = await route.async_hook_url(
                        self.hass, self._webhook_id, chosen
                    )
                except route.RouteUnavailableError:
                    errors[CONF_ROUTE] = "route_unavailable"
                else:
                    self._route = chosen
                    self._wait(
                        views.STAGE_CREATE,
                        post_url=f"{GITHUB}/organizations/{org}/settings/apps/new"
                        if org
                        else f"{GITHUB}/settings/apps/new",
                        manifest=build_manifest(name, hook_url),
                    )
                    return await self.async_step_create()
        schema = vol.Schema(
            {
                vol.Required(CONF_APP_NAME, default=self._default_name): str,
                vol.Optional(CONF_ORGANIZATION, default=""): str,
                vol.Required(CONF_ROUTE, default=routes[0]): _route_selector(routes),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_create(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """GitHub shows the app for confirmation; views.CreatedView brings back the code."""
        if user_input is None:
            return self.async_external_step(
                step_id="create", url=f"{self._base()}{views.START_PATH}/{self._nonce}"
            )
        self._code = str(user_input["code"])
        self._forget()
        return self.async_external_step_done(next_step_id="convert")

    async def async_step_convert(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        try:
            self._creds = await convert_manifest(
                async_get_clientsession(self.hass), self._code
            )
        except GitHubError:
            return self.async_abort(reason="conversion_failed")
        return await self.async_step_install()

    async def async_step_install(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """The owner installs the app; views.InstalledView offers each installation id."""
        creds = self._credentials()
        if user_input is not None:
            try:
                installation = await self._app(creds).installation(
                    int(user_input["installation_id"])
                )
            except GitHubError:
                installation = None
            if installation is not None and not installation.get("suspended_at"):
                self._installation = installation
                self._forget()
                return self.async_external_step_done(next_step_id="finish")
        if self._nonce is None:
            self._wait(views.STAGE_INSTALL)
        return self.async_external_step(
            step_id="install", url=f"{creds.html_url}/installations/new"
        )

    async def async_step_finish(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        creds = self._credentials()
        installation = self._installation or {}
        account = installation.get("account") or {}
        client = InstallationClient(self._app(creds), int(installation.get("id", 0)))
        try:
            await client.repositories()
        except GitHubError:
            return self.async_abort(reason="cannot_connect")
        data = {
            CONF_APP_ID: creds.app_id,
            CONF_APP_SLUG: creds.slug,
            CONF_APP_URL: creds.html_url,
            CONF_PRIVATE_KEY: creds.pem,
            CONF_WEBHOOK_SECRET: creds.webhook_secret,
            CONF_WEBHOOK_ID: self._webhook_id,
            CONF_CLOUDHOOK: self._route == ROUTE_CLOUD,
            CONF_INSTALLATION_ID: int(installation["id"]),
            CONF_ACCOUNT: str(account.get("login") or ""),
            CONF_ACCOUNT_ID: int(account.get("id") or 0),
        }
        await self.async_set_unique_id(str(data[CONF_ACCOUNT_ID]))
        if self.source == SOURCE_REAUTH:
            self._abort_if_unique_id_mismatch(reason="wrong_account")
            entry = self._get_reauth_entry()
            if entry.data.get(CONF_CLOUDHOOK) and self._route != ROUTE_CLOUD:
                # The new app delivers to the external URL; the old cloudhook has no owner left.
                await route.async_delete_cloudhook(
                    self.hass, entry.data[CONF_WEBHOOK_ID]
                )
            return self.async_update_reload_and_abort(entry, data_updates=data)
        self._abort_if_unique_id_configured()
        self._keep_hook = True
        return self.async_create_entry(
            title=str(data[CONF_ACCOUNT]),
            data=data,
            options={
                CONF_ME: [str(data[CONF_ACCOUNT])],
                CONF_NOTIFY_TARGETS: [],
                CONF_PERSISTENT: True,
                CONF_NOTIFY_ON: list(DEFAULT_NOTIFY_ON),
                CONF_INCLUDE_BOTS: False,
                CONF_INCLUDE_OWN: False,
            },
        )

    # ------------------------------------------------------------ reauth

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        self._keep_hook = True
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Reinstall the app, or make a new one on the same webhook when GitHub no longer accepts its key."""
        entry = self._get_reauth_entry()
        placeholders = {"account": entry.title}
        if user_input is None:
            return self.async_show_form(
                step_id="reauth_confirm", description_placeholders=placeholders
            )
        self._webhook_id = entry.data[CONF_WEBHOOK_ID]
        self._route = ROUTE_CLOUD if entry.data.get(CONF_CLOUDHOOK) else ROUTE_EXTERNAL
        creds = AppCredentials(
            app_id=int(entry.data[CONF_APP_ID]),
            slug=entry.data[CONF_APP_SLUG],
            html_url=entry.data[CONF_APP_URL],
            pem=entry.data[CONF_PRIVATE_KEY],
            webhook_secret=entry.data[CONF_WEBHOOK_SECRET],
            owner_login=entry.data[CONF_ACCOUNT],
            owner_id=int(entry.data[CONF_ACCOUNT_ID]),
        )
        app = self._app(creds)
        try:
            await app.get_app()
            installation = await app.installation(int(entry.data[CONF_INSTALLATION_ID]))
        except GitHubAuthError:
            return await self.async_step_user()
        except GitHubNotFoundError:
            self._creds = creds
            return await self.async_step_install()
        except GitHubError:
            return self.async_show_form(
                step_id="reauth_confirm",
                description_placeholders=placeholders,
                errors={"base": "cannot_connect"},
            )
        if installation.get("suspended_at"):
            return self.async_show_form(
                step_id="reauth_confirm",
                description_placeholders=placeholders,
                errors={"base": "suspended"},
            )
        self._creds = creds
        self._installation = installation
        return await self.async_step_finish()

    # ------------------------------------------------------- reconfigure

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Move the webhook between Home Assistant Cloud and the external URL."""
        entry = self._get_reconfigure_entry()
        current = ROUTE_CLOUD if entry.data.get(CONF_CLOUDHOOK) else ROUTE_EXTERNAL
        errors: dict[str, str] = {}
        if user_input is not None:
            chosen = str(user_input[CONF_ROUTE])
            app = GitHubApp(
                async_get_clientsession(self.hass),
                int(entry.data[CONF_APP_ID]),
                entry.data[CONF_PRIVATE_KEY],
            )
            try:
                url = await route.async_hook_url(
                    self.hass, entry.data[CONF_WEBHOOK_ID], chosen
                )
                await app.update_hook_url(url)
            except route.RouteUnavailableError:
                errors[CONF_ROUTE] = "route_unavailable"
            except GitHubError as err:
                errors["base"] = (
                    "invalid_auth"
                    if isinstance(err, GitHubAuthError)
                    else "cannot_connect"
                )
                if chosen == ROUTE_CLOUD and not entry.data.get(CONF_CLOUDHOOK):
                    # GitHub still delivers to the external URL; the cloudhook just made is not used.
                    await route.async_delete_cloudhook(
                        self.hass, entry.data[CONF_WEBHOOK_ID]
                    )
            else:
                if chosen != ROUTE_CLOUD and entry.data.get(CONF_CLOUDHOOK):
                    await route.async_delete_cloudhook(
                        self.hass, entry.data[CONF_WEBHOOK_ID]
                    )
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_CLOUDHOOK: chosen == ROUTE_CLOUD}
                )
        routes = route.available_routes(self.hass) or [current]
        default = current if current in routes else routes[0]
        schema = vol.Schema(
            {vol.Required(CONF_ROUTE, default=default): _route_selector(routes)}
        )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=schema,
            errors=errors,
            description_placeholders={"account": entry.title},
        )

    # ----------------------------------------------------------- helpers

    def _credentials(self) -> AppCredentials:
        if self._creds is None:
            raise RuntimeError("the flow reached a step before the app existed")
        return self._creds

    def _app(self, creds: AppCredentials) -> GitHubApp:
        return GitHubApp(async_get_clientsession(self.hass), creds.app_id, creds.pem)

    def _base(self) -> str:
        """The origin the browser uses for Home Assistant; GitHub sends it back there."""
        request = current_request.get()
        if request is not None:
            return f"{request.scheme}://{request.host}"
        try:
            return get_url(self.hass)
        except NoURLAvailableError:
            return ""

    def _wait(self, stage: str, **extra: Any) -> None:
        views.async_setup(self.hass)
        self._forget()
        self._nonce = secrets.token_urlsafe(24)
        views.pending(self.hass)[self._nonce] = views.Pending(
            flow_id=self.flow_id, stage=stage, **extra
        )

    def _forget(self) -> None:
        if self._nonce is not None:
            views.pending(self.hass).pop(self._nonce, None)
            self._nonce = None

    @callback
    @override
    def async_remove(self) -> None:
        self._forget()
        if self._webhook_id and not self._keep_hook and self._route == ROUTE_CLOUD:
            self.hass.async_create_background_task(
                route.async_delete_cloudhook(self.hass, self._webhook_id),
                f"{DOMAIN} drop unused cloudhook",
            )


class InboxOptionsFlow(OptionsFlowWithReload):
    """Who you are on GitHub, and where and when notifications go."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            me = sorted(model.logins(user_input.get(CONF_ME) or []))
            return self.async_create_entry(
                data={
                    **user_input,
                    CONF_ME: me or [self.config_entry.data[CONF_ACCOUNT]],
                }
            )
        current = self.config_entry.options
        me = list(current.get(CONF_ME) or [self.config_entry.data[CONF_ACCOUNT]])
        chosen = list(current.get(CONF_NOTIFY_TARGETS, []))
        # `send_message` addresses notify entities and needs an entity id.
        services = set(self.hass.services.async_services_for_domain("notify"))
        targets = sorted((services - {"send_message"}) | set(chosen))
        schema = vol.Schema(
            {
                vol.Required(CONF_ME, default=me): TextSelector(
                    TextSelectorConfig(multiple=True)
                ),
                vol.Optional(CONF_NOTIFY_TARGETS, default=chosen): SelectSelector(
                    SelectSelectorConfig(
                        options=targets,
                        multiple=True,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(
                    CONF_PERSISTENT, default=bool(current.get(CONF_PERSISTENT, True))
                ): BooleanSelector(),
                vol.Required(
                    CONF_NOTIFY_ON,
                    default=list(current.get(CONF_NOTIFY_ON, DEFAULT_NOTIFY_ON)),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=list(NOTIFY_CHOICES),
                        multiple=True,
                        translation_key="notify_on",
                        mode=SelectSelectorMode.LIST,
                    )
                ),
                vol.Required(
                    CONF_INCLUDE_BOTS,
                    default=bool(current.get(CONF_INCLUDE_BOTS, False)),
                ): BooleanSelector(),
                vol.Required(
                    CONF_INCLUDE_OWN, default=bool(current.get(CONF_INCLUDE_OWN, False))
                ): BooleanSelector(),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
