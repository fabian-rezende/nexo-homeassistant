"""Cortinas: endpoints BLIND, comandados pela abertura como a voz (100 = aberta).

Como no comando de voz: não há "parar", e a cortina precisa estar calibrada no
configurador para o hub aceitar a posição.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.cover import ATTR_POSITION, CoverDeviceClass, CoverEntity, CoverEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import NexoConfigEntry
from .entity import NexoEntity, endpoints_of


async def async_setup_entry(hass: HomeAssistant, entry: NexoConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    hub = entry.runtime_data
    async_add_entities(NexoCover(hub, e) for e in endpoints_of(hub, "B"))


class NexoCover(NexoEntity, CoverEntity):
    _attr_device_class = CoverDeviceClass.CURTAIN
    _attr_supported_features = CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.SET_POSITION

    @property
    def current_cover_position(self) -> int | None:
        st = self._state
        return None if st is None else st["position"]

    @property
    def is_closed(self) -> bool | None:
        st = self._state
        return None if st is None else st["position"] <= 0

    async def _go(self, opening: int) -> None:
        await self._command({"rangeValue": opening}, {"position": opening})

    async def async_open_cover(self, **kwargs: Any) -> None:
        await self._go(100)

    async def async_close_cover(self, **kwargs: Any) -> None:
        await self._go(0)

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        await self._go(int(kwargs[ATTR_POSITION]))
