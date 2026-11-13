"""Acha o sistema Nexo a partir de qualquer hub da casa. Sem dependência do Home Assistant.

A tela de comando de voz pode ser aberta em qualquer hub (relé ou dimmer) e cada hub guarda a
sua própria lista. Por isso a descoberta lê o slot 6 de TODOS os hubs da malha e junta os itens.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
from typing import Any
import urllib.request

from .nexo import PORT, Holder, NexoError, NexoLink, VoiceFormatError, VoiceList, merge_lists


class NoPrincipal(Exception):
    """Nenhum hub da malha tem lista de comando de voz com itens marcados."""


@dataclass
class System:
    holders: list[Holder]             # hubs com lista; o primeiro é a âncora
    lists: list[VoiceList]            # na mesma ordem
    hubs: list[dict[str, Any]]        # todos os hubs da malha (devices.loaddevices)
    appdata: dict[str, Any]           # ambientes (crl.appdata/1)
    ignored: dict[str, str] = field(default_factory=dict)   # SN -> sha de slot 6 que não é lista de voz

    @property
    def sn(self) -> str:
        """Identidade do sistema: o SN da âncora."""
        return self.holders[0].sn

    @property
    def host(self) -> str:
        return self.holders[0].host

    def hub(self, sn: str) -> dict[str, Any]:
        return next((h for h in self.hubs if h.get("sn") == sn), {})


def _get_json(url: str) -> Any:
    with urllib.request.urlopen(url, timeout=10) as resp:  # noqa: S310 - IP local da casa
        return json.loads(resp.read().decode("utf-8"))


async def fetch_appdata(host: str) -> dict[str, Any]:
    """Ambientes da casa (GET /appdata). Vazio se o hub não responder: só perde as áreas."""
    try:
        data = await asyncio.to_thread(_get_json, f"http://{host}/appdata")
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _order(h: dict[str, Any], main: str) -> tuple:
    """Âncora: o relé apontado como principal nos ambientes, depois relés, depois o resto; empate pelo SN."""
    sn = str(h.get("sn") or "")
    return (str(h.get("addr") or "").upper() != main, not sn.startswith("NXHR1"), sn)


async def find_system(host: str, port: int = PORT) -> System:
    """Conecta em `host`, lista os hubs da malha e lê a lista de voz de cada um.

    ConnectionError: `host` não atendeu. NoPrincipal: nenhum hub com itens marcados.
    """
    appdata = await fetch_appdata(host)
    link = NexoLink(host, port)
    await link.start()
    try:
        hubs = await link.hubs()
        main = str(appdata.get("main") or "").upper()
        found: list[tuple[dict[str, Any], VoiceList]] = []
        ignored: dict[str, str] = {}
        for h in sorted((h for h in hubs if h.get("sn") and h.get("addr") and h.get("ip") and h.get("on", True)),
                        key=lambda h: _order(h, main)):
            sn, addr = str(h["sn"]), str(h["addr"]).upper()
            try:
                total, sha = await link.voice_head(addr)
            except NexoError:
                continue
            if not total:
                continue
            try:
                vl = await _read_direct(str(h["ip"]), addr, link, port)
            except VoiceFormatError:
                ignored[sn] = sha   # slot 6 com outra coisa (no dimmer o espaço é dividido com os níveis)
                continue
            except (NexoError, ConnectionError):
                continue            # não deu para ler agora: a conferência de 5 min tenta de novo
            if vl.sn and vl.sn != sn:
                ignored[sn] = sha   # lista copiada de outro hub: não é dele
                continue
            if vl.endpoints:
                found.append((h, vl))
    finally:
        await link.stop()
    if not found:
        raise NoPrincipal("no_voice")
    holders = [Holder(sn=str(h["sn"]), addr=str(h["addr"]).upper(), host=str(h["ip"]), sha256=vl.sha256) for h, vl in found]
    if not appdata or holders[0].host != host:
        appdata = await fetch_appdata(holders[0].host) or appdata
    return System(holders=holders, lists=[vl for _, vl in found], hubs=hubs, appdata=appdata, ignored=ignored)


async def _read_direct(host: str, addr: str, fallback: NexoLink, port: int = PORT) -> VoiceList:
    """Lê a lista conectando no próprio hub (a malha só espera 400 ms); se não abrir, pela malha."""
    link = NexoLink(host, port)
    try:
        await link.start()
    except ConnectionError:
        return await fallback.read_voice(addr)
    try:
        return await link.read_voice(addr)
    finally:
        await link.stop()


def endpoints_of_system(system: System) -> list:
    return merge_lists(system.lists)


def room_of(appdata: dict[str, Any], addr: str, out: int) -> str | None:
    """Nome do ambiente de uma saída (circuits[] -> nó do tipo ambiente)."""
    nodes = {n.get("id"): n for n in appdata.get("nodes") or [] if isinstance(n, dict)}
    for c in appdata.get("circuits") or []:
        if str(c.get("addr") or "").upper() == addr and c.get("out") == out:
            node = nodes.get(c.get("node"))
            if node and node.get("kind") == "ambiente":
                return str(node.get("name") or "") or None
    return None
