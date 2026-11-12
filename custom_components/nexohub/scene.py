"""Cenas: endpoints SCENE, ativadas como a voz ({"scene":"activate"})."""
from __future__ import annotations

from typing import Any

from homeassistant.components.scene import Scene
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import NexoConfigEntry
from .entity import NexoEntity, endpoints_of


async def async_setup_entry(hass: HomeAssistant, entry: NexoConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    hub = entry.runtime_data
    async_add_entities(NexoScene(hub, e) for e in endpoints_of(hub, "S"))


class NexoScene(NexoEntity, Scene):
    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._hub.listen("conn", self._conn))

    @callback
    def _conn(self) -> None:
        self.async_write_ha_state()

    async def async_activate(self, **kwargs: Any) -> None:
        await self._command({"scene": "activate"}, None)
