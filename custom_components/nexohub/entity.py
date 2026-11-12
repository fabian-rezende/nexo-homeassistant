"""Base das entidades: um endpoint marcado para voz vira um dispositivo do Home Assistant."""
from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any

from homeassistant.core import CALLBACK_TYPE, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.event import async_call_later

from .const import DOMAIN, MANUFACTURER
from .discovery import room_of
from .nexo import Endpoint, NexoError, NexoHub

OPTIMISTIC_S = 4.0     # sem confirmação nesse tempo, volta ao estado conhecido
CONFIRM_S = 0.6        # report do endpoint depois do comando (o ack do hub é sempre ok)
MODEL = {"R": "Relé (voz)", "D": "Dimmer (voz)", "B": "Cortina (voz)", "S": "Cena (voz)"}


def endpoints_of(hub: NexoHub, kind: str) -> list[Endpoint]:
    return [e for e in hub.endpoints.values() if e.kind == kind]


class NexoEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_name = None

    def __init__(self, hub: NexoHub, ep: Endpoint) -> None:
        self._hub = hub
        self._ep = ep
        self._optimistic: tuple[dict[str, Any], float] | None = None
        self._timer: CALLBACK_TYPE | None = None
        self._attr_unique_id = ep.uuid
        info = DeviceInfo(
            identifiers={(DOMAIN, ep.uuid)},
            name=ep.name,
            manufacturer=MANUFACTURER,
            model=MODEL.get(ep.kind),
            suggested_area=None if ep.kind == "S" else room_of(getattr(hub, "appdata", {}), ep.addr, ep.out),
        )
        dev_id = getattr(hub, "principal_device_id", None)
        if dev_id:
            info["via_device_id"] = dev_id
        self._attr_device_info = info

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._hub.listen(self._ep.uuid, self._changed))
        self.async_on_remove(self._drop)

    @callback
    def _changed(self) -> None:
        self._drop()
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        return self._hub.connected

    @property
    def _state(self) -> dict[str, Any] | None:
        if self._optimistic is not None:
            if time.monotonic() < self._optimistic[1]:
                return self._optimistic[0]
            self._optimistic = None
        return self._hub.states.get(self._ep.uuid)

    @callback
    def _drop(self) -> None:
        self._optimistic = None
        if self._timer is not None:
            self._timer()
            self._timer = None

    async def _command(self, voice_state: dict[str, Any], expected: dict[str, Any] | None) -> None:
        """Manda como a voz chega; mostra o esperado até confirmar e confere com o report."""
        try:
            await self._hub.command(self._ep, voice_state)
        except (ConnectionError, OSError) as err:
            raise HomeAssistantError(f"Hub principal do Nexo sem conexão: {err}") from err
        if expected is None:
            return
        self._drop()
        self._optimistic = (expected, time.monotonic() + OPTIMISTIC_S)

        @callback
        def expired(_now: Any) -> None:
            self._timer = None
            self._optimistic = None
            self.async_write_ha_state()

        self._timer = async_call_later(self.hass, OPTIMISTIC_S, expired)
        self.async_write_ha_state()
        self.hass.async_create_task(self._confirm())

    async def _confirm(self) -> None:
        await asyncio.sleep(CONFIRM_S)
        with contextlib.suppress(NexoError, ConnectionError, OSError):
            await self._hub.report(self._ep)
