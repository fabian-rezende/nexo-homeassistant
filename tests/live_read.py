"""Teste ao vivo, só leitura: acha o principal a partir de qualquer hub e mostra o estado de cada endpoint.

Uso: python live_read.py <ip de qualquer Nexo Hub>. Não comanda nada: só report:"state".
"""
import asyncio
import sys

import conftest  # noqa: F401
from nexohub_pure.discovery import find_principal, room_of
from nexohub_pure.nexo import NexoHub


async def main(host: str) -> None:
    p = await find_principal(host)
    print(f"principal: {p.sn} ({p.name}) em {p.host}, endereço {p.addr}, fw {p.fw}")
    print(f"hubs na malha: {[(h['sn'], h['ip']) for h in p.hubs]}")
    print(f"lista de voz: {len(p.voice.endpoints)} marcados, sha {p.voice.sha256[:12]}…")
    hub = NexoHub(p.host)
    hub.addr = p.addr
    hub.set_endpoints(p.voice)
    await hub.start()
    await asyncio.sleep(2)
    for e in p.voice.endpoints:
        st = hub.states.get(e.uuid)
        room = room_of(p.appdata, e.addr, e.out) if e.kind != "S" else "-"
        print(f"  [{e.kind}] {e.name:22} hub {e.addr} saída {e.out:2}  área {room!s:14} estado {st}")
    await hub.stop()


asyncio.run(main(sys.argv[1]))
