"""Conexão com o hub principal do Nexo e os endpoints de voz. Sem dependência do Home Assistant.

O Home Assistant enxerga só os endpoints MARCADOS na tela de comando de voz do configurador
(slot 6 do principal, formato crl.voice/1) e comanda por eles exatamente como a voz chega:

  comando  {"source":"Alexa","endpoint_id":"<SN>:<UUID>","state":{...},"id":N}
  estado   {"source":"Alexa","report":"state","endpoint_id":"<SN>:<UUID>","id":N}

O principal acha o hub dono pelo UUID e repassa pela malha; UUID fora da lista é recusado
pelo próprio firmware (userVoice.c). A resposta do comando é sempre {"alexa":"ok"}, mesmo
recusado: a confirmação é o report logo depois, como o MCP faz.

Para atualizar na hora, os avisos da malha que chegam a toda conexão local
({"evt":"output"|"dimmer"|"motor","addr",...}) viram o estado do endpoint pelo `map` da lista.
"""
from __future__ import annotations

import asyncio
import base64
from collections.abc import Callable
import contextlib
from dataclasses import dataclass
import hashlib
import json
import logging
import socket
from typing import Any

_LOGGER = logging.getLogger(__name__)

PORT = 4998
CONNECT_TIMEOUT_S = 8
REPLY_TIMEOUT_S = 3.0
RX_TIMEOUT_S = 75           # sem nenhuma linha nesse tempo, a conexão está morta (há o report de 60 s)
REPORT_EVERY_S = 60
CHUNK = 1500
VOICE_SLOT = 6
TYPES = {"RELE": "R", "DIMMER": "D", "BLIND": "B", "SCENE": "S"}

Listener = Callable[[], None]


class NexoError(Exception):
    """O hub respondeu com erro ou não respondeu."""


@dataclass(frozen=True)
class Endpoint:
    """Um item marcado para voz."""

    uuid: str          # maiúsculo, sem o SN
    endpoint_id: str   # "<SN do principal>:<UUID>"
    name: str          # nome falado
    kind: str          # R, D, B ou S
    addr: str          # hub dono ("56:5B:47"); na cena, o principal
    out: int           # saída a partir de 0; cortina: a saída par; cena: o id
    description: str = ""


@dataclass(frozen=True)
class VoiceList:
    sn: str                       # SN do principal
    sha256: str
    endpoints: list[Endpoint]


def parse_voice(raw: bytes, sha256: str) -> VoiceList:
    """Slot 6 -> só os endpoints marcados (`map` + `devices`; `rows` é a grade da tela e fica de fora)."""
    doc = json.loads(raw.decode("utf-8"))
    if not isinstance(doc, dict) or doc.get("schema") != "crl.voice/1":
        raise NexoError("a lista de voz do hub principal não está no formato crl.voice/1")
    by_uuid = {str(m.get("u", "")).upper(): m for m in doc.get("map") or [] if isinstance(m, dict)}
    out: list[Endpoint] = []
    for dev in doc.get("devices") or []:
        ep_id = str(dev.get("endpoint_id") or "")
        uuid = ep_id.rpartition(":")[2].upper()
        m = by_uuid.get(uuid)
        kind = TYPES.get(str(dev.get("device_type") or "").upper())
        if not m or not kind or m.get("t") != kind:
            continue
        out.append(Endpoint(
            uuid=uuid, endpoint_id=ep_id, name=str(dev.get("friendly_name") or uuid),
            kind=kind, addr=str(m.get("a") or "").upper(), out=int(m.get("o") or 0),
            description=str(dev.get("description") or ""),
        ))
    return VoiceList(sn=str(doc.get("sn") or ""), sha256=sha256, endpoints=out)


def state_from_report(kind: str, st: Any) -> dict[str, Any] | None:
    """`state` do report -> estado neutro. None = o principal não sabe."""
    if not isinstance(st, dict):
        return None
    if kind == "R" and "powerState" in st:
        return {"on": st["powerState"] == "ON"}
    if kind == "D" and "brightness" in st:
        b = int(st["brightness"])
        return {"on": b > 0, "brightness": b}
    if kind == "B" and "rangeValue" in st:
        return {"position": int(st["rangeValue"])}  # abertura: 100 = aberta
    return None


class NexoHub:
    """Uma conexão NDJSON com o hub principal."""

    def __init__(self, host: str, port: int = PORT) -> None:
        self.host = host
        self.port = port
        self.connected = False
        self.addr: str | None = None         # endereço do principal (3 bytes do MAC)
        self.endpoints: dict[str, Endpoint] = {}
        self.states: dict[str, dict[str, Any]] = {}
        self.on_list_changed: Callable[[], None] | None = None
        self.voice_sha: str | None = None
        self._by_out: dict[tuple[str, str, int], Endpoint] = {}
        self._writer: asyncio.StreamWriter | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._next_id = 1000
        self._task: asyncio.Task | None = None
        self._first = asyncio.Event()
        self._closing = False
        self._listeners: dict[str, set[Listener]] = {}

    # ------------------------------------------------------------------ endpoints
    def set_endpoints(self, voice: VoiceList) -> None:
        self.voice_sha = voice.sha256
        self.endpoints = {e.uuid: e for e in voice.endpoints}
        self._by_out = {}
        for e in voice.endpoints:
            if e.kind in ("R", "D"):
                self._by_out[("o", e.addr, e.out)] = e
            elif e.kind == "B":
                self._by_out[("m", e.addr, e.out // 2)] = e

    # ------------------------------------------------------------------ ciclo de vida
    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name=f"nexohub-{self.host}")
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._first.wait(), CONNECT_TIMEOUT_S + 2)
        if not self.connected:
            await self.stop()
            raise ConnectionError(f"o hub principal em {self.host}:{self.port} não atendeu")

    async def stop(self) -> None:
        self._closing = True
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None
        self._set_connected(False)

    async def _run(self) -> None:
        backoff = 3
        while not self._closing:
            try:
                reader, writer = await asyncio.wait_for(asyncio.open_connection(self.host, self.port), CONNECT_TIMEOUT_S)
            except (OSError, asyncio.TimeoutError) as err:
                _LOGGER.debug("Nexo %s: sem conexão (%s)", self.host, err)
                self._first.set()
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)
                continue
            backoff = 3
            sock = writer.get_extra_info("socket")
            if sock is not None:
                with contextlib.suppress(OSError):
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            self._writer = writer
            self._set_connected(True)
            self._first.set()
            chores = asyncio.create_task(self._chores())
            try:
                while True:
                    raw = await asyncio.wait_for(reader.readline(), RX_TIMEOUT_S)
                    if not raw:
                        break
                    self._on_line(raw)
            except (OSError, asyncio.TimeoutError, ValueError, asyncio.IncompleteReadError) as err:
                _LOGGER.debug("Nexo %s: conexão caiu (%s)", self.host, err)
            finally:
                chores.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await chores
                self._writer = None
                for fut in self._pending.values():
                    if not fut.done():
                        fut.set_exception(ConnectionError("conexão caiu"))
                self._pending.clear()
                writer.close()
                self._set_connected(False)
            if not self._closing:
                await asyncio.sleep(backoff)

    async def _chores(self) -> None:
        """Ao conectar: confere a lista e lê o estado de todos; depois, report a cada 60 s e lista a cada 5 min."""
        rounds = 0
        while True:
            with contextlib.suppress(ConnectionError, NexoError, OSError):
                if rounds % 5 == 0 and self.voice_sha and self.addr:
                    sha = (await self.request(self.addr, "html.get", {"slot": VOICE_SLOT}))["data"].get("sha256")
                    if sha and sha != self.voice_sha and self.on_list_changed:
                        _LOGGER.info("Nexo %s: a lista de voz mudou", self.host)
                        self.on_list_changed()
                await self.report_all()
            rounds += 1
            await asyncio.sleep(REPORT_EVERY_S)

    # ------------------------------------------------------------------ envio e resposta
    async def _send(self, msg: dict) -> int:
        writer = self._writer
        if writer is None or writer.is_closing():
            raise ConnectionError("o hub principal do Nexo não está conectado")
        self._next_id += 1
        if self._next_id > 2_000_000_000:
            self._next_id = 1000
        msg["id"] = self._next_id
        fut = asyncio.get_running_loop().create_future()
        self._pending[self._next_id] = fut
        writer.write((json.dumps(msg, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))
        await writer.drain()
        return self._next_id

    async def _wait(self, rid: int, timeout: float) -> dict:
        fut = self._pending[rid]
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError as err:
            raise NexoError("o hub não respondeu") from err
        finally:
            self._pending.pop(rid, None)

    async def request(self, addr: str, cmd: str, data: dict | None = None, timeout: float = REPLY_TIMEOUT_S) -> dict:
        msg: dict[str, Any] = {"targetaddr": addr, "cmd": cmd}
        if data is not None:
            msg["data"] = data
        reply = await self._wait(await self._send(msg), timeout)
        if reply.get("ok") is False or "error" in reply:
            raise NexoError(str(reply.get("error") or "erro"))
        return reply

    async def command(self, ep: Endpoint, state: dict[str, Any]) -> None:
        """Comando pelo endpoint, como a voz chega. O hub sempre responde ok."""
        rid = await self._send({"source": "Alexa", "endpoint_id": ep.endpoint_id, "state": state})
        with contextlib.suppress(NexoError):
            await self._wait(rid, REPLY_TIMEOUT_S)

    async def report(self, ep: Endpoint) -> dict[str, Any] | None:
        """Estado do endpoint na memória do principal (None = desconhecido)."""
        rid = await self._send({"source": "Alexa", "report": "state", "endpoint_id": ep.endpoint_id})
        reply = await self._wait(rid, REPLY_TIMEOUT_S)
        st = state_from_report(ep.kind, reply.get("state"))
        if st is not None:
            self._set_state(ep, st)
        return st

    async def report_all(self) -> None:
        for ep in list(self.endpoints.values()):
            if ep.kind != "S":
                with contextlib.suppress(NexoError):
                    await self.report(ep)

    async def read_voice(self, addr: str) -> VoiceList:
        """Baixa o slot 6 em fatias, confere o sha256 e devolve só os marcados."""
        head = (await self.request(addr, "html.get", {"slot": VOICE_SLOT}))["data"]
        if not head.get("present") or not head.get("len"):
            raise NexoError("o hub principal está sem a lista de comando de voz")
        total, sha = int(head["len"]), str(head.get("sha256") or "")
        raw, frm, size = b"", 0, CHUNK
        while frm < total:
            try:
                d = (await self.request(addr, "html.read", {"slot": VOICE_SLOT, "from": frm, "max": size, "b64": True}))["data"]
            except NexoError as err:
                if "memory" in str(err) and size > 256:
                    size //= 2
                    continue
                raise
            chunk = base64.b64decode(d.get("chunk") or "")
            if not chunk:
                break
            raw += chunk
            frm += len(chunk)
            if d.get("eof"):
                break
        if sha and hashlib.sha256(raw).hexdigest() != sha.lower():
            raise NexoError("a lista de voz veio incompleta (sha256 não confere)")
        return parse_voice(raw, sha)

    # ------------------------------------------------------------------ recepção
    def _on_line(self, raw: bytes) -> None:
        try:
            msg = json.loads(raw)
        except ValueError:
            return
        if not isinstance(msg, dict):
            return
        rid = msg.get("id")
        if isinstance(rid, int) and rid in self._pending:
            fut = self._pending[rid]
            if not fut.done():
                fut.set_result(msg)
            return
        evt = msg.get("evt")
        addr = str(msg.get("addr") or "").upper()
        if evt == "output":
            ep = self._by_out.get(("o", addr, int(msg.get("output", -1))))
            if ep and ep.kind == "R" and msg.get("event") in ("on", "off"):
                self._set_state(ep, {"on": msg["event"] == "on"})
            elif ep and ep.kind == "D" and msg.get("event") == "off":
                self._set_state(ep, {"on": False, "brightness": 0})
        elif evt == "dimmer":
            ep = self._by_out.get(("o", addr, int(msg.get("out", -1))))
            if ep and ep.kind == "D" and "pct" in msg:
                pct = int(msg["pct"])
                self._set_state(ep, {"on": pct > 0, "brightness": pct})
        elif evt == "motor":
            ep = self._by_out.get(("m", addr, int(msg.get("pair", -1))))
            pos = msg.get("pos")
            if ep and isinstance(pos, int) and pos >= 0:
                # o hub mede o quanto desceu, em ‰ (0 = toda em cima); a abertura é o contrário
                self._set_state(ep, {"position": max(0, min(100, 100 - (pos + 5) // 10))})

    def _set_state(self, ep: Endpoint, st: dict[str, Any]) -> None:
        self.states[ep.uuid] = st
        self._notify(ep.uuid)

    # ------------------------------------------------------------------ ouvintes
    def listen(self, key: str, cb: Listener) -> Callable[[], None]:
        """Avisa cb quando muda o endpoint `key` (UUID) ou a conexão."""
        self._listeners.setdefault(key, set()).add(cb)

        def remove() -> None:
            self._listeners.get(key, set()).discard(cb)

        return remove

    def _notify(self, key: str) -> None:
        for cb in list(self._listeners.get(key, ())):
            cb()

    def _set_connected(self, state: bool) -> None:
        if state == self.connected:
            return
        self.connected = state
        _LOGGER.info("Nexo %s: %s", self.host, "conectado" if state else "desconectado")
        for cbs in list(self._listeners.values()):
            for cb in list(cbs):
                cb()
