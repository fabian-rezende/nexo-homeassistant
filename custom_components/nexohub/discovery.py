"""Acha o hub principal a partir de qualquer hub da casa. Sem dependência do Home Assistant."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
from typing import Any
import urllib.request

from .nexo import NexoError, NexoHub, VoiceList


class NoPrincipal(Exception):
    """Não foi possível achar o hub principal."""


@dataclass
class Principal:
    host: str
    addr: str
    sn: str
    name: str
    model: str
    fw: str
    hubs: list[dict[str, Any]]      # todos os hubs da malha (devices.loaddevices)
    appdata: dict[str, Any]          # ambientes (crl.appdata/1)
    voice: VoiceList


def _get_json(url: str) -> Any:
    with urllib.request.urlopen(url, timeout=10) as resp:  # noqa: S310 - IP local informado pelo usuário
        return json.loads(resp.read().decode("utf-8"))


async def fetch_appdata(host: str) -> dict[str, Any]:
    """Ambientes da casa (GET /appdata). Vazio se o hub não responder: só perde as áreas."""
    try:
        data = await asyncio.to_thread(_get_json, f"http://{host}/appdata")
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


async def _hub_list(hub: NexoHub) -> list[dict[str, Any]]:
    hubs: list[dict[str, Any]] = []
    frm = 0
    while True:
        d = (await hub.request("*", "devices.loaddevices", {"from": frm, "max": 25}))["data"]
        page = d.get("list") or []
        hubs.extend(page)
        frm += len(page)
        if not page or frm >= int(d.get("total") or 0):
            return hubs


async def find_principal(host: str) -> Principal:
    """Conecta em `host`, acha o principal (o `main` dos ambientes) e lê a lista de voz dele.

    ConnectionError: `host` não atendeu. NoPrincipal: não achou o principal ou ele está sem lista.
    """
    appdata = await fetch_appdata(host)
    probe = NexoHub(host)
    await probe.start()
    try:
        hubs = await _hub_list(probe)
    except NexoError as err:
        raise NoPrincipal(str(err)) from err
    finally:
        await probe.stop()

    # O principal é o hub que tem a lista de voz gravada no slot 6 com o próprio SN. O `main` dos
    # ambientes só define a ordem da busca: um hub secundário pode guardar ambientes antigos que
    # apontam para ele mesmo (visto na bancada).
    main = str(appdata.get("main") or "").upper()
    cand = sorted(
        (h for h in hubs if str(h.get("sn") or "").startswith("NXHR1") and h.get("ip")),
        key=lambda h: (str(h.get("addr") or "").upper() != main, not h.get("self")),
    )
    empty = False
    for h in cand:
        hub = NexoHub(str(h["ip"]))
        try:
            await hub.start()
        except ConnectionError:
            continue
        try:
            voice = await hub.read_voice(str(h["addr"]).upper())
        except NexoError:
            continue
        finally:
            await hub.stop()
        if voice.sn and voice.sn != h.get("sn"):
            continue  # lista copiada de outro hub: não é o principal
        if not voice.endpoints:
            empty = True
            continue
        appdata = await fetch_appdata(str(h["ip"])) or appdata
        return Principal(
            host=str(h["ip"]), addr=str(h["addr"]).upper(), sn=str(h.get("sn") or voice.sn),
            name=str(h.get("name") or ""), model="Nexo Hub Relé" if str(h.get("sn", "")).startswith("NXHR1") else "Nexo Hub",
            fw=str(h.get("fw") or ""), hubs=hubs, appdata=appdata, voice=voice,
        )
    raise NoPrincipal("no_voice" if empty else "no_principal")


def room_of(appdata: dict[str, Any], addr: str, out: int) -> str | None:
    """Nome do ambiente de uma saída (circuits[] -> nó do tipo ambiente)."""
    nodes = {n.get("id"): n for n in appdata.get("nodes") or [] if isinstance(n, dict)}
    for c in appdata.get("circuits") or []:
        if str(c.get("addr") or "").upper() == addr and c.get("out") == out:
            node = nodes.get(c.get("node"))
            if node and node.get("kind") == "ambiente":
                return str(node.get("name") or "") or None
    return None
