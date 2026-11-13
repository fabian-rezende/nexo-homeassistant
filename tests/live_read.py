"""Teste ao vivo, só leitura: lê a lista de voz de todos os hubs e mostra o estado de cada endpoint.

Uso: python live_read.py <ip de qualquer Nexo Hub>. Não comanda nada: só report:"state".
"""
import asyncio
import sys

import conftest  # noqa: F401
from nexohub_pure.discovery import endpoints_of_system, find_system, room_of
from nexohub_pure.nexo import NexoSystem


async def main(host: str) -> None:
    found = await find_system(host)
    print(f"hubs na malha: {[(h['sn'], h['ip']) for h in found.hubs]}")
    for h, vl in zip(found.holders, found.lists):
        print(f"lista de voz em {h.sn} ({h.host}): {len(vl.endpoints)} marcados, sha {h.sha256[:12]}…")
    if found.ignored:
        print(f"slot 6 sem lista de voz (ignorado): {sorted(found.ignored)}")
    eps = endpoints_of_system(found)
    nx = NexoSystem(found.holders, eps, ignored=found.ignored)
    await nx.start()
    await asyncio.sleep(2)
    for e in eps:
        room = room_of(found.appdata, e.addr, e.out) if e.kind != "S" else "-"
        print(f"  [{e.kind}] {e.name:22} lista {e.holder}  saída {e.addr}/{e.out:<2} área {room!s:14} estado {nx.states.get(e.uuid)}")
    print("listas mudaram:", await nx.lists_changed())
    await nx.stop()


asyncio.run(main(sys.argv[1]))
