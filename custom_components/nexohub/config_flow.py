"""Configuração: IP de qualquer Nexo Hub da casa, ou descoberta pelo DHCP."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo

from .const import DOMAIN
from .discovery import NoPrincipal, find_principal


class NexoConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._host = ""
        self._name = ""

    async def _probe(self, host: str) -> str | None:
        """Acha o principal; fixa o SN dele como id único. Devolve o código do erro ou None."""
        try:
            p = await find_principal(host)
        except ConnectionError:
            return "cannot_connect"
        except NoPrincipal as err:
            return "no_voice" if str(err) == "no_voice" else "no_principal"
        await self.async_set_unique_id(p.sn)
        self._abort_if_unique_id_configured(updates={CONF_HOST: p.host})
        self._host = p.host
        self._name = p.name or p.sn
        return None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            error = await self._probe(user_input[CONF_HOST].strip())
            if error is None:
                return self.async_create_entry(title=f"Nexo - {self._name}", data={CONF_HOST: self._host})
            errors["base"] = error
        schema = vol.Schema({vol.Required(CONF_HOST, default=(user_input or {}).get(CONF_HOST, "")): str})
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_dhcp(self, discovery_info: DhcpServiceInfo) -> ConfigFlowResult:
        error = await self._probe(discovery_info.ip)
        if error is not None:
            return self.async_abort(reason=error)
        self.context["title_placeholders"] = {"name": self._name}
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title=f"Nexo - {self._name}", data={CONF_HOST: self._host})
        self._set_confirm_only()
        return self.async_show_form(step_id="confirm", description_placeholders={"name": self._name, "host": self._host})
