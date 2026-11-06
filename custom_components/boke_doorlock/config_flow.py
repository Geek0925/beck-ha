"""Config flow: sign in with the iCSee Home account (phone/e-mail + password)."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.data_entry_flow import FlowResult

from .const import (
    CONF_ALERT_RESET,
    CONF_APP_KEY,
    CONF_APP_SECRET,
    CONF_AREA_CODE,
    CONF_HOST,
    CONF_MQTT_ENABLED,
    CONF_MQTT_HOST,
    CONF_MQTT_LOG_PAYLOADS,
    CONF_MQTT_PASSWORD,
    CONF_MQTT_PORT,
    CONF_MQTT_TLS,
    CONF_MQTT_TOPICS,
    CONF_MQTT_USERNAME,
    CONF_PASSWORD,
    CONF_PASSWORD_ENCODING,
    CONF_RECORD_INTERVAL,
    CONF_SCAN_INTERVAL,
    CONF_SNS,
    CONF_USERNAME,
    CONF_UUID,
    DEFAULT_ALERT_RESET,
    DEFAULT_APP_KEY,
    DEFAULT_UUID,
    DEFAULT_APP_SECRET,
    DEFAULT_AREA_CODE,
    DEFAULT_HOST,
    DEFAULT_MQTT_HOST,
    DEFAULT_MQTT_PORT,
    DEFAULT_MQTT_TOPICS,
    DEFAULT_PASSWORD_ENCODING,
    DEFAULT_RECORD_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    NAME,
)
from .jfcloud import JFCloudAuthError, JFCloudClient, JFCloudConnectionError, JFCloudError

_LOGGER = logging.getLogger(__name__)


def _account_schema(defaults: dict[str, Any] | None = None, *, advanced: bool = False) -> vol.Schema:
    defaults = defaults or {}
    fields: dict[Any, Any] = {
        vol.Required(CONF_USERNAME, default=defaults.get(CONF_USERNAME, "")): str,
        vol.Required(CONF_PASSWORD, default=defaults.get(CONF_PASSWORD, "")): str,
        vol.Optional(CONF_AREA_CODE, default=defaults.get(CONF_AREA_CODE, DEFAULT_AREA_CODE)): str,
        vol.Optional(CONF_SNS, default=defaults.get(CONF_SNS, "")): str,
    }
    if advanced:
        fields.update(
            {
                vol.Optional(CONF_HOST, default=defaults.get(CONF_HOST, DEFAULT_HOST)): str,
                vol.Optional(
                    CONF_PASSWORD_ENCODING,
                    default=defaults.get(CONF_PASSWORD_ENCODING, DEFAULT_PASSWORD_ENCODING),
                ): vol.In(["md5", "plain"]),
                vol.Optional(
                    CONF_APP_KEY, default=defaults.get(CONF_APP_KEY, DEFAULT_APP_KEY)
                ): str,
                vol.Optional(
                    CONF_APP_SECRET, default=defaults.get(CONF_APP_SECRET, DEFAULT_APP_SECRET)
                ): str,
            }
        )
    return vol.Schema(fields)


class BokeDoorLockConfigFlow(ConfigFlow, domain=DOMAIN):
    """Two steps: credentials, then optional advanced overrides."""

    VERSION = 1

    def __init__(self) -> None:
        self._pending: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            credentials = {
                CONF_USERNAME: user_input[CONF_USERNAME].strip(),
                CONF_PASSWORD: user_input[CONF_PASSWORD],
                CONF_AREA_CODE: user_input.get(CONF_AREA_CODE, DEFAULT_AREA_CODE).strip(),
                CONF_SNS: (user_input.get(CONF_SNS) or "").strip(),
            }
            payload, error = await self._try_login(credentials)
            if payload is not None:
                self._pending = credentials
                return await self._finish(credentials, payload)
            errors["base"] = error

        return self.async_show_form(
            step_id="user", data_schema=_account_schema(), errors=errors
        )

    async def async_step_advanced(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Advanced: API host, password encoding and the signing credentials."""
        errors: dict[str, str] = {}
        if user_input is not None:
            credentials = {**self._pending, **user_input}
            credentials[CONF_SNS] = self._pending.get(CONF_SNS, "")
            payload, error = await self._try_login(credentials)
            if payload is not None:
                return await self._finish(credentials, payload)
            errors["base"] = error

        return self.async_show_form(
            step_id="advanced",
            data_schema=_account_schema(self._pending, advanced=True),
            errors=errors,
        )

    async def _try_login(
        self, data: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str | None]:
        client = JFCloudClient(
            uuid=data.get(CONF_UUID) or DEFAULT_UUID,
            app_key=data.get(CONF_APP_KEY) or DEFAULT_APP_KEY,
            app_secret=data.get(CONF_APP_SECRET) or DEFAULT_APP_SECRET,
            host=data.get(CONF_HOST) or "cn-jvss.jftechws.com",
            login_name=data[CONF_USERNAME],
            password=data[CONF_PASSWORD],
            area_code=data.get(CONF_AREA_CODE, DEFAULT_AREA_CODE),
            password_encoding=data.get(CONF_PASSWORD_ENCODING, DEFAULT_PASSWORD_ENCODING),
        )
        try:
            payload = await client.login()
            devices = await client.list_devices()
            if not devices:
                return None, "no_devices"
            return payload, None
        except JFCloudAuthError as err:
            _LOGGER.warning("login rejected: %s", err)
            return None, "invalid_auth"
        except JFCloudConnectionError as err:
            _LOGGER.warning("login transport error: %s", err)
            return None, "cannot_connect"
        except JFCloudError as err:
            _LOGGER.warning("login failed: %s", err)
            return None, "cloud_error"
        finally:
            await client.close()

    async def _finish(
        self, credentials: dict[str, Any], payload: dict[str, Any]
    ) -> FlowResult:
        username = credentials[CONF_USERNAME]
        await self.async_set_unique_id(username.lower())
        self._abort_if_unique_id_configured()

        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        return self.async_create_entry(
            title=f"{NAME} · {username}",
            data={
                CONF_USERNAME: username,
                CONF_PASSWORD: credentials[CONF_PASSWORD],
                CONF_AREA_CODE: credentials.get(CONF_AREA_CODE, DEFAULT_AREA_CODE),
                CONF_PASSWORD_ENCODING: credentials.get(
                    CONF_PASSWORD_ENCODING, DEFAULT_PASSWORD_ENCODING
                ),
                CONF_HOST: credentials.get(CONF_HOST, DEFAULT_HOST),
                CONF_APP_KEY: credentials.get(CONF_APP_KEY, DEFAULT_APP_KEY),
                CONF_APP_SECRET: credentials.get(CONF_APP_SECRET, DEFAULT_APP_SECRET),
                CONF_SNS: credentials.get(CONF_SNS, ""),
                CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
                CONF_UUID: data.get("uuid") or "",
                "token": data.get("accessToken") or data.get("token") or "",
            },
        )

    @staticmethod
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        return BokeOptionsFlow(entry)


class BokeOptionsFlow(OptionsFlow):
    """Polling, record harvesting and the optional MQTT push channel."""

    def __init__(self, entry: ConfigEntry) -> None:
        self.entry = entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = {**self.entry.data, **self.entry.options}
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_SCAN_INTERVAL,
                    default=current.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): vol.All(int, vol.Range(min=0, max=600)),
                vol.Optional(
                    CONF_RECORD_INTERVAL,
                    default=current.get(CONF_RECORD_INTERVAL, DEFAULT_RECORD_INTERVAL),
                ): vol.All(int, vol.Range(min=0, max=3600)),
                vol.Optional(
                    CONF_ALERT_RESET,
                    default=int(current.get(CONF_ALERT_RESET, DEFAULT_ALERT_RESET)),
                ): vol.All(int, vol.Range(min=0, max=86400)),
                vol.Optional(CONF_SNS, default=current.get(CONF_SNS, "")): str,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
