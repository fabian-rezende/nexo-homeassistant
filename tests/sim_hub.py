"""Dois hubs simulados, cada um com a sua lista de voz (relé em 127.0.0.1 e dimmer em 127.0.0.2).

Confere: lê as listas dos dois; só os marcados entram; o mesmo circuito marcado nas duas listas
vira um item só; cada comando sai no envelope da voz e vai ao hub que guarda a lista do item; o
report e os avisos da malha atualizam o endpoint certo; um slot 6 com outra coisa (memória dos
níveis do dimmer) é ignorado. Uso: python sim_hub.py
"""
import asyncio
import base64
import hashlib
import json

import conftest  # noqa: F401
from nexohub_pure.discovery import endpoints_of_system, find_system
from nexohub_pure.nexo import NexoSystem

RELE = {"sn": "NXHR1-000000-000009", "addr": "56:5B:47", "ip": "127.0.0.1"}
DIM = {"sn": "NXHD1-000000-000009", "addr": "2F:67:1F", "ip": "127.0.0.2"}
LIXO = {"sn": "NXHD1-000000-000008", "addr": "11:22:33", "ip": "127.0.0.3"}   # slot 6 com os níveis do dimmer
U = "AAAAAAAA-0000-5000-8000-0000000000"


def voice(sn: str, items: list[tuple[str, str, int, str, str, str]]) -> bytes:
    """items: (uuid final, addr, out, t, device_type, nome)."""
    return json.dumps({
        "schema": "crl.voice/1", "sn": sn,
        "map": [{"u": U + u, "a": a, "o": o, "t": t} for u, a, o, t, _, _ in items],
        "devices": [{"endpoint_id": f"{sn}:{U}{u}", "friendly_name": n, "device_type": dt} for u, _, _, _, dt, n in items],
        "rows": [{"u": U + "99", "a": "56:5B:47", "o": 5, "t": "R", "on": False, "n": "Desmarcado"}],
    }).encode()


SLOTS = {
    RELE["addr"]: voice(RELE["sn"], [("01", "56:5B:47", 0, "R", "RELE", "Luz"), ("03", "56:5B:47", 8, "B", "BLIND", "Cortina"),
                                     ("04", "56:5B:47", 1, "S", "SCENE", "Cena"), ("02", "2F:67:1F", 3, "D", "DIMMER", "Dimmer pelo relé")]),
    # o dimmer marcou o mesmo canal 3 (mesmo UUID) e mais um canal só dele
    DIM["addr"]: voice(DIM["sn"], [("02", "2F:67:1F", 3, "D", "DIMMER", "Dimmer pelo dimmer"), ("05", "2F:67:1F", 0, "D", "DIMMER", "Armario")]),
    LIXO["addr"]: bytes(range(256)) * 4,
}


async def main() -> None:
    got: dict[str, list[dict]] = {"127.0.0.1": [], "127.0.0.2": [], "127.0.0.3": []}
    memory: dict[str, dict] = {U + "01": {"powerState": "OFF"}, U + "03": {"rangeValue": 30}, U + "05": {"powerState": "ON", "brightness": 60}}

    def make(ip: str, me: dict):
        async def hub(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            def reply(obj: dict) -> None:
                writer.write((json.dumps(obj) + "\n").encode())

            while raw := await reader.readline():
                m = json.loads(raw)
                got[ip].append(m)
                rid, cmd = m.get("id"), m.get("cmd")
                if cmd == "devices.loaddevices":
                    lst = [{**h, "on": True, "self": h is me} for h in (RELE, DIM, LIXO)]
                    reply({"id": rid, "data": {"total": 3, "list": lst}, "ok": True})
                elif cmd == "html.get":
                    raw6 = SLOTS.get(m["targetaddr"], b"")
                    reply({"id": rid, "data": {"slot": 6, "present": bool(raw6), "len": len(raw6), "sha256": hashlib.sha256(raw6).hexdigest()}, "ok": True})
                elif cmd == "html.read":
                    d, raw6 = m["data"], SLOTS[m["targetaddr"]]
                    part = raw6[d["from"]:d["from"] + d["max"]]
                    reply({"id": rid, "data": {"chunk": base64.b64encode(part).decode(), "eof": d["from"] + len(part) >= len(raw6)}, "ok": True})
                elif "source" in m:
                    prefix, _, uuid = m["endpoint_id"].rpartition(":")
                    if prefix != me["sn"]:
                        reply({"alexa": "ok", "id": rid, **({"endpoint_id": m["endpoint_id"], "state": None} if m.get("report") else {})})
                    elif m.get("report") == "state":
                        reply({"alexa": "ok", "id": rid, "endpoint_id": m["endpoint_id"], "state": memory.get(uuid)})
                    else:
                        reply({"alexa": "ok", "id": rid})
                await writer.drain()
        return hub

    servers = [await asyncio.start_server(make(h["ip"], h), h["ip"], 0 if h is RELE else port) for h in (RELE,) for port in [0]]
    port = servers[0].sockets[0].getsockname()[1]
    servers += [await asyncio.start_server(make(h["ip"], h), h["ip"], port) for h in (DIM, LIXO)]

    found = await find_system("127.0.0.2", port)        # entra pelo dimmer: a âncora continua sendo o relé
    assert [h.sn for h in found.holders] == [RELE["sn"], DIM["sn"]], found.holders
    assert found.sn == RELE["sn"]
    assert LIXO["sn"] in found.ignored, "slot 6 sem lista de voz tem de ser ignorado"
    eps = endpoints_of_system(found)
    names = sorted(e.name for e in eps)
    assert names == ["Armario", "Cena", "Cortina", "Dimmer pelo relé", "Luz"], names   # canal repetido: um só; desmarcado: fora
    by = {e.name: e for e in eps}
    assert by["Armario"].holder == DIM["sn"] and by["Luz"].holder == RELE["sn"]

    nx = NexoSystem(found.holders, eps, port=port, ignored=found.ignored)
    await nx.start()
    for ip in got:
        got[ip].clear()
    await nx.report_all()
    assert nx.states[by["Luz"].uuid] == {"on": False}
    assert nx.states[by["Cortina"].uuid] == {"position": 30}
    assert nx.states[by["Armario"].uuid] == {"on": True, "brightness": 60}, "report do item do dimmer vai ao dimmer"

    await nx.command(by["Luz"], {"powerState": "ON"})
    await nx.command(by["Armario"], {"brightness": 40})
    await nx.command(by["Cena"], {"scene": "activate"})
    cmds = {ip: [(m["endpoint_id"], m["state"]) for m in lst if "source" in m and "report" not in m] for ip, lst in got.items()}
    assert cmds["127.0.0.1"] == [(f"{RELE['sn']}:{U}01", {"powerState": "ON"}), (f"{RELE['sn']}:{U}04", {"scene": "activate"})], cmds
    assert cmds["127.0.0.2"] == [(f"{DIM['sn']}:{U}05", {"brightness": 40})], cmds
    assert all("cmd" not in m for lst in got.values() for m in lst if "source" in m)

    # avisos da malha (chegam pela âncora) -> endpoint pelo map
    nx._on_message({"evt": "output", "addr": "56:5B:47", "output": 0, "event": "on"})
    nx._on_message({"evt": "dimmer", "addr": "2F:67:1F", "out": 0, "level": 102, "pct": 40})
    nx._on_message({"evt": "motor", "addr": "56:5B:47", "pair": 4, "state": "stop", "pos": 250})
    assert nx.states[by["Luz"].uuid] == {"on": True}
    assert nx.states[by["Armario"].uuid] == {"on": True, "brightness": 40}
    assert nx.states[by["Cortina"].uuid] == {"position": 75}

    assert not await nx.lists_changed(), "nada mudou"
    SLOTS[DIM["addr"]] = voice(DIM["sn"], [("05", "2F:67:1F", 0, "D", "DIMMER", "Armario")])   # desmarcou no dimmer
    assert await nx.lists_changed(), "mudança na lista do dimmer tem de ser notada"

    await nx.stop()
    for s in servers:
        s.close()
    print("OK: listas de todos os hubs; repetido vira um; comando e report no hub da lista; lixo do dimmer ignorado; mudança notada")


asyncio.run(main())
