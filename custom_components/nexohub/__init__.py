"""Integração Controlart Nexo Hub: os endpoints marcados para voz em qualquer hub, comandados como a voz chega."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .const import DOMAIN, MANUFACTURER
from .discovery import NoPrincipal, endpoints_of_system, find_system
from .nexo import NexoSystem

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.COVER, Platform.LIGHT, Platform.SCENE]

type NexoConfigEntry = ConfigEntry[NexoSystem]


async def async_setup_entry(hass: HomeAssistant, entry: NexoConfigEntry) -> bool:
    try:
        found = await find_system(entry.data[CONF_HOST])
    except (ConnectionError, NoPrincipal) as err:
        raise ConfigEntryNotReady(f"sistema Nexo: {err}") from err
    if found.sn != entry.unique_id and entry.unique_id not in {h.sn for h in found.holders}:
        _LOGGER.warning("Nexo: o sistema configurado como %s agora responde como %s", entry.unique_id, found.sn)
    if found.host != entry.data[CONF_HOST]:
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_HOST: found.host})

    system = NexoSystem(found.holders, endpoints_of_system(found), ignored=found.ignored)
    system.appdata = found.appdata  # type: ignore[attr-defined]

    @callback
    def list_changed() -> None:
        hass.config_entries.async_schedule_reload(entry.entry_id)

    system.on_list_changed = list_changed
    try:
        await system.start()
    except ConnectionError as err:
        raise ConfigEntryNotReady(str(err)) from err
    entry.runtime_data = system

    # um dispositivo por hub que guarda lista; os itens ficam ligados ao hub da lista deles
    dev_reg = dr.async_get(hass)
    system.hub_device_ids = {}  # type: ignore[attr-defined]
    for h in found.holders:
        info = found.hub(h.sn)
        system.hub_device_ids[h.sn] = dev_reg.async_get_or_create(  # type: ignore[attr-defined]
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, h.sn)},
            manufacturer=MANUFACTURER,
            model="Nexo Hub Dimmer" if h.sn.startswith("NXHD1") else "Nexo Hub Relé",
            name=str(info.get("name") or f"Nexo {h.sn}"),
            serial_number=h.sn,
            sw_version=str(info.get("fw") or "") or None,
            configuration_url=f"http://{h.host}/",
        ).id
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _remove_stale(hass, entry, system)
    return True


def _remove_stale(hass: HomeAssistant, entry: NexoConfigEntry, system: NexoSystem) -> None:
    """Tira do Home Assistant o que foi desmarcado (e o hub que deixou de ter lista)."""
    keep = set(system.endpoints)
    ent_reg = er.async_get(hass)
    for ent in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
        if ent.unique_id not in keep:
            ent_reg.async_remove(ent.entity_id)
    keep_dev = keep | {h.sn for h in system.holders}
    dev_reg = dr.async_get(hass)
    for dev in dr.async_entries_for_config_entry(dev_reg, entry.entry_id):
        if not {i[1] for i in dev.identifiers if i[0] == DOMAIN} & keep_dev:
            dev_reg.async_update_device(dev.id, remove_config_entry_id=entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: NexoConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        await entry.runtime_data.stop()
    return ok
