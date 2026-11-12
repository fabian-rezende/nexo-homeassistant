"""Integração Controlart Nexo Hub: os endpoints marcados para voz, comandados como a voz chega."""
from __future__ import annotations

import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .const import DOMAIN, MANUFACTURER
from .discovery import NoPrincipal, find_principal
from .nexo import NexoHub

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.COVER, Platform.LIGHT, Platform.SCENE]

type NexoConfigEntry = ConfigEntry[NexoHub]


async def async_setup_entry(hass: HomeAssistant, entry: NexoConfigEntry) -> bool:
    try:
        p = await find_principal(entry.data[CONF_HOST])
    except (ConnectionError, NoPrincipal) as err:
        raise ConfigEntryNotReady(f"hub principal do Nexo: {err}") from err
    if p.sn != entry.unique_id:
        raise ConfigEntryNotReady(f"o hub principal agora é {p.sn}, não {entry.unique_id}")
    if p.host != entry.data[CONF_HOST]:
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_HOST: p.host})

    hub = NexoHub(p.host)
    hub.addr = p.addr
    hub.set_endpoints(p.voice)
    hub.appdata = p.appdata  # type: ignore[attr-defined]

    @callback
    def list_changed() -> None:
        hass.config_entries.async_schedule_reload(entry.entry_id)

    hub.on_list_changed = list_changed
    try:
        await hub.start()
    except ConnectionError as err:
        raise ConfigEntryNotReady(str(err)) from err
    entry.runtime_data = hub

    dev_reg = dr.async_get(hass)
    hub.principal_device_id = dev_reg.async_get_or_create(  # type: ignore[attr-defined]
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, p.sn)},
        manufacturer=MANUFACTURER,
        model=p.model,
        name=p.name or f"Nexo {p.sn}",
        serial_number=p.sn,
        sw_version=p.fw or None,
        configuration_url=f"http://{p.host}/",
    ).id
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _remove_stale(hass, entry, hub)
    return True


def _remove_stale(hass: HomeAssistant, entry: NexoConfigEntry, hub: NexoHub) -> None:
    """Tira do Home Assistant o que foi desmarcado na tela de comando de voz."""
    keep = set(hub.endpoints)
    ent_reg = er.async_get(hass)
    for ent in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
        if ent.unique_id not in keep:
            ent_reg.async_remove(ent.entity_id)
    dev_reg = dr.async_get(hass)
    principal = entry.unique_id
    for dev in dr.async_entries_for_config_entry(dev_reg, entry.entry_id):
        ids = {i[1] for i in dev.identifiers if i[0] == DOMAIN}
        if not ids & (keep | {principal}):
            dev_reg.async_update_device(dev.id, remove_config_entry_id=entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: NexoConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        await entry.runtime_data.stop()
    return ok

