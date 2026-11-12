"""Hub principal simulado: lista de voz no slot 6, comandos por endpoint e avisos da malha.

Confere: só os marcados entram; o comando sai no envelope da voz; o report atualiza o estado;
os avisos output/dimmer/motor viram o estado do endpoint certo. Uso: python sim_hub.py
"""
import asyncio
import base64
import hashlib
import json

import conftest  # noqa: F401
from nexohub_pure.nexo import NexoHub

SN = "NXHR1-000000-000009"
DOC = {
    "schema": "crl.voice/1", "sn": SN,
    "map": [
        {"u": "AAAAAAAA-0000-5000-8000-000000000001", "a": "56:5B:47", "o": 0, "t": "R"},
        {"u": "AAAAAAAA-0000-5000-8000-000000000002", "a": "2F:67:1F", "o": 3, "t": "D"},
        {"u": "AAAAAAAA-0000-5000-8000-000000000003", "a": "56:5B:47", "o": 8, "t": "B"},
        {"u": "AAAAAAAA-0000-5000-8000-000000000004", "a": "56:5B:47", "o": 1, "t": "S"},
    ],
    "devices": [
        {"endpoint_id": f"{SN}:AAAAAAAA-0000-5000-8000-000000000001", "friendly_name": "Luz", "device_type": "RELE"},
        {"endpoint_id": f"{SN}:AAAAAAAA-0000-5000-8000-000000000002", "friendly_name": "Dimmer", "device_type": "DIMMER"},
        {"endpoint_id": f"{SN}:AAAAAAAA-0000-5000-8000-000000000003", "friendly_name": "Cortina", "device_type": "BLIND"},
        {"endpoint_id": f"{SN}:AAAAAAAA-0000-5000-8000-000000000004", "friendly_name": "Cena", "device_type": "SCENE"},
    ],
    # a grade da tela: o desmarcado NÃO pode entrar
    "rows": [{"u": "BBBBBBBB-0000-5000-8000-000000000009", "a": "56:5B:47", "o": 5, "t": "R", "on": False, "n": "Desmarcado"}],
}
RAW = json.dumps(DOC).encode()
SHA = hashlib.sha256(RAW).hexdigest()


async def main() -> None:
    got: list[dict] = []
    memory = {"AAAAAAAA-0000-5000-8000-000000000001": {"powerState": "OFF"},
              "AAAAAAAA-0000-5000-8000-000000000003": {"rangeValue": 30}}

    async def hub(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        def reply(obj: dict) -> None:
            writer.write((json.dumps(obj) + "\n").encode())

        while raw := await reader.readline():
            m = json.loads(raw)
            got.append(m)
            rid = m.get("id")
            if m.get("cmd") == "html.get":
                reply({"id": rid, "data": {"slot": 6, "present": True, "len": len(RAW), "sha256": SHA}, "ok": True})
            elif m.get("cmd") == "html.read":
                d = m["data"]
                part = RAW[d["from"]:d["from"] + d["max"]]
                reply({"id": rid, "data": {"chunk": base64.b64encode(part).decode(), "eof": d["from"] + len(part) >= len(RAW)}, "ok": True})
            elif "source" in m and m.get("report") == "state":
                uuid = m["endpoint_id"].rpartition(":")[2]
                reply({"alexa": "ok", "id": rid, "endpoint_id": m["endpoint_id"], "state": memory.get(uuid)})
            elif "source" in m:
                uuid = m["endpoint_id"].rpartition(":")[2]
                if "powerState" in m["state"] and uuid.endswith("1"):
                    memory[uuid] = {"powerState": m["state"]["powerState"]}
                reply({"alexa": "ok", "id": rid})
            await writer.drain()

    server = await asyncio.start_server(hub, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    nx = NexoHub("127.0.0.1", port)
    await nx.start()

    voice = await nx.read_voice("56:5B:47")
    names = sorted(e.name for e in voice.endpoints)
    assert names == ["Cena", "Cortina", "Dimmer", "Luz"], names   # o desmarcado de `rows` ficou de fora
    nx.addr = "56:5B:47"
    nx.set_endpoints(voice)
    luz, dim, cort, cena = (next(e for e in voice.endpoints if e.name == n) for n in ("Luz", "Dimmer", "Cortina", "Cena"))

    await nx.report_all()
    assert nx.states[luz.uuid] == {"on": False}
    assert nx.states[cort.uuid] == {"position": 30}
    assert dim.uuid not in nx.states, "report null = desconhecido"

    await nx.command(luz, {"powerState": "ON"})
    await nx.command(dim, {"brightness": 40})
    await nx.command(cort, {"rangeValue": 100})
    await nx.command(cena, {"scene": "activate"})
    cmds = [m for m in got if "source" in m and "report" not in m]
    assert all("cmd" not in m and "targetaddr" not in m for m in cmds), "envelope da voz não leva cmd"
    assert [(m["endpoint_id"].rpartition(":")[2], m["state"]) for m in cmds] == [
        (luz.uuid, {"powerState": "ON"}), (dim.uuid, {"brightness": 40}),
        (cort.uuid, {"rangeValue": 100}), (cena.uuid, {"scene": "activate"})], cmds
    await nx.report(luz)
    assert nx.states[luz.uuid] == {"on": True}

    # avisos da malha -> endpoint pelo map
    nx._on_line(b'{"evt":"output","addr":"56:5B:47","output":0,"event":"off"}')
    nx._on_line(b'{"evt":"dimmer","addr":"2F:67:1F","out":3,"level":102,"pct":40}')
    nx._on_line(b'{"evt":"motor","addr":"56:5B:47","pair":4,"state":"stop","pos":250}')
    nx._on_line(b'{"evt":"output","addr":"56:5B:47","output":5,"event":"on"}')   # saída sem endpoint
    assert nx.states[luz.uuid] == {"on": False}
    assert nx.states[dim.uuid] == {"on": True, "brightness": 40}
    assert nx.states[cort.uuid] == {"position": 75}, nx.states[cort.uuid]   # 25 % descida = 75 % aberta
    assert len(nx.states) == 3

    await nx.stop()
    server.close()
    print("OK: só marcados; comandos no envelope da voz; report e avisos atualizam o endpoint certo")


asyncio.run(main())
