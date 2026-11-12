"""Luzes: endpoints RELE (liga/desliga) e DIMMER (brilho)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import NexoConfigEntry
from .entity import NexoEntity, endpoints_of


async def async_setup_entry(hass: HomeAssistant, entry: NexoConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    hub = entry.runtime_data
    async_add_entities(NexoLight(hub, e) for e in endpoints_of(hub, "R") + endpoints_of(hub, "D"))


class NexoLight(NexoEntity, LightEntity):
    def __init__(self, hub, ep) -> None:
        super().__init__(hub, ep)
        mode = ColorMode.BRIGHTNESS if ep.kind == "D" else ColorMode.ONOFF
        self._attr_color_mode = mode
        self._attr_supported_color_modes = {mode}

    @property
    def is_on(self) -> bool | None:
        st = self._state
        return None if st is None else st["on"]

    @property
    def brightness(self) -> int | None:
        st = self._state
        if self._ep.kind != "D" or st is None or "brightness" not in st:
            return None
        return round(st["brightness"] * 255 / 100)

    async def async_turn_on(self, **kwargs: Any) -> None:
        bright = kwargs.get(ATTR_BRIGHTNESS)
        if self._ep.kind == "D" and bright is not None:
            pct = max(1, round(bright * 100 / 255))
            await self._command({"brightness": pct}, {"on": True, "brightness": pct})
        elif self._ep.kind == "D":
            # como a Alexa: "ligar" sem nível acende em 100 %
            await self._command({"powerState": "ON"}, {"on": True, "brightness": 100})
        else:
            await self._command({"powerState": "ON"}, {"on": True})

    async def async_turn_off(self, **kwargs: Any) -> None:
        off = {"on": False, "brightness": 0} if self._ep.kind == "D" else {"on": False}
        await self._command({"powerState": "OFF"}, off)
