"""Conexão com os Nexo Hub e os endpoints de voz. Sem dependência do Home Assistant.

O Home Assistant enxerga só os endpoints MARCADOS na tela de comando de voz do configurador e
comanda por eles exatamente como a voz chega. A tela pode ser aberta em qualquer hub (relé ou
dimmer), e cada hub guarda a SUA lista no slot 6 (formato crl.voice/1). Por isso a integração
lê a lista de todos os hubs da malha e junta os itens.

  comando  {"source":"Alexa","endpoint_id":"<SN do hub da lista>:<UUID>","state":{...},"id":N}
  estado   {"source":"Alexa","report":"state","endpoint_id":"<SN>:<UUID>","id":N}

O envelope só vale no hub cujo SN abre o endpoint_id (o firmware recusa "endpoint de outro
aparelho"): cada item é comandado no hub que guarda a lista dele, numa conexão própria. A
resposta do comando é sempre {"alexa":"ok"}, mesmo recusado; a confirmação é o report logo
depois, como o MCP faz.

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
SCAN_EVERY = 5              # a cada 5 rodadas de report (5 min) confere as listas de todos os hubs
CHUNK = 1500
VOICE_SLOT = 6
TYPES = {"RELE": "R", "DIMMER": "D", "BLIND": "B", "SCENE": "S"}

Listener = Callable[[], None]


class NexoError(Exception):
    """O hub respondeu com erro ou não respondeu."""


class VoiceFormatError(NexoError):
    """O slot 6 tem outra coisa que não uma lista de voz (no dimmer o espaço é dividido)."""


@dataclass(frozen=True)
class Endpoint:
    """Um item marcado para voz."""

    uuid: str          # maiúsculo, sem o SN
    endpoint_id: str   # "<SN do hub que guarda a lista>:<UUID>"
    holder: str        # SN do hub que guarda a lista (para onde vai o comando)
    name: str          # nome falado
    kind: str          # R, D, B ou S
    addr: str          # hub dono da saída ("56:5B:47"); na cena, o hub que roda a cena
    out: int           # saída a partir de 0; cortina: a saída par; cena: o id
    description: str = ""


@dataclass(frozen=True)
class VoiceList:
    sn: str                       # SN do hub que guarda a lista
    sha256: str
    endpoints: list[Endpoint]


def parse_voice(raw: bytes, sha256: str) -> VoiceList:
    """Slot 6 -> só os endpoints marcados (`map` + `devices`; `rows` é a grade da tela e fica de fora)."""
    doc = json.loads(raw.decode("utf-8"))
    if not isinstance(doc, dict) or doc.get("schema") != "crl.voice/1":
        raise VoiceFormatError("o slot 6 não tem uma lista de voz crl.voice/1")
    sn = str(doc.get("sn") or "")
    by_uuid = {str(m.get("u", "")).upper(): m for m in doc.get("map") or [] if isinstance(m, dict)}
    out: list[Endpoint] = []
    for dev in doc.get("devices") or []:
        ep_id = str(dev.get("endpoint_id") or "")
        prefix, _, uuid = ep_id.rpartition(":")
        uuid = uuid.upper()
        m = by_uuid.get(uuid)
        kind = TYPES.get(str(dev.get("device_type") or "").upper())
        if not m or not kind or m.get("t") != kind:
            continue
        out.append(Endpoint(
            uuid=uuid, endpoint_id=ep_id, holder=prefix or sn, name=str(dev.get("friendly_name") or uuid),
            kind=kind, addr=str(m.get("a") or "").upper(), out=int(m.get("o") or 0),
            description=str(dev.get("description") or ""),
        ))
    return VoiceList(sn=sn, sha256=sha256, endpoints=out)


def merge_lists(lists: list[VoiceList]) -> list[Endpoint]:
    """Junta as listas na ordem dada; o mesmo circuito marcado em duas listas vira um item só."""
    seen: dict[str, Endpoint] = {}
    for vl in lists:
        for ep in vl.endpoints:
            seen.setdefault(ep.uuid, ep)
    return list(seen.values())


def state_from_report(kind: str, st: Any) -> dict[str, Any] | None:
    """`state` do report -> estado neutro. None = o hub não sabe."""
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


class NexoLink:
    """Uma conexão NDJSON com um hub, com reconexão."""

    def __init__(self, host: str, port: int = PORT, on_message: Callable[[dict], None] | None = None,
                 on_state: Callable[[bool], None] | None = None) -> None:
        self.host = host
        self.port = port
        self.connected = False
        self._on_message = on_message
        self._on_state = on_state
        self._writer: asyncio.StreamWriter | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._next_id = 1000
        self._task: asyncio.Task | None = None
        self._first = asyncio.Event()
        self._closing = False

    async def start(self, require: bool = True) -> None:
        """Abre a conexão. require=False: não espera nem falha, segue tentando em segundo plano."""
        self._task = asyncio.create_task(self._run(), name=f"nexohub-{self.host}")
        if not require:
            return
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._first.wait(), CONNECT_TIMEOUT_S + 2)
        if not self.connected:
            await self.stop()
            raise ConnectionError(f"o Nexo Hub em {self.host}:{self.port} não atendeu")

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
            try:
                while True:
                    raw = await asyncio.wait_for(reader.readline(), RX_TIMEOUT_S)
                    if not raw:
                        break
                    self._on_line(raw)
            except (OSError, asyncio.TimeoutError, ValueError, asyncio.IncompleteReadError) as err:
                _LOGGER.debug("Nexo %s: conexão caiu (%s)", self.host, err)
            finally:
                self._writer = None
                for fut in self._pending.values():
                    if not fut.done():
                        fut.set_exception(ConnectionError("conexão caiu"))
                self._pending.clear()
                writer.close()
                self._set_connected(False)
            if not self._closing:
                await asyncio.sleep(backoff)

    def _set_connected(self, state: bool) -> None:
        if state == self.connected:
            return
        self.connected = state
        _LOGGER.info("Nexo %s: %s", self.host, "conectado" if state else "desconectado")
        if self._on_state:
            self._on_state(state)

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
        if self._on_message:
            self._on_message(msg)

    async def send(self, msg: dict) -> int:
        writer = self._writer
        if writer is None or writer.is_closing():
            raise ConnectionError(f"o Nexo Hub {self.host} não está conectado")
        self._next_id += 1
        if self._next_id > 2_000_000_000:
            self._next_id = 1000
        msg["id"] = self._next_id
        self._pending[self._next_id] = asyncio.get_running_loop().create_future()
        writer.write((json.dumps(msg, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))
        await writer.drain()
        return self._next_id

    async def wait(self, rid: int, timeout: float = REPLY_TIMEOUT_S) -> dict:
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
        reply = await self.wait(await self.send(msg), timeout)
        if reply.get("ok") is False or "error" in reply:
            raise NexoError(str(reply.get("error") or "erro"))
        return reply

    async def voice_head(self, addr: str) -> tuple[int, str]:
        """(tamanho, sha256) do slot 6 do hub `addr`; (0, "") sem lista."""
        d = (await self.request(addr, "html.get", {"slot": VOICE_SLOT}))["data"]
        if not d.get("present") or not d.get("len"):
            return 0, ""
        return int(d["len"]), str(d.get("sha256") or "").lower()

    async def read_voice(self, addr: str) -> VoiceList:
        """Baixa o slot 6 do hub `addr` em fatias, confere o sha256 e devolve só os marcados."""
        total, sha = await self.voice_head(addr)
        if not total:
            raise NexoError("o hub está sem lista de comando de voz")
        for attempt in range(2):
            raw = await self._download(addr, total)
            if not sha or hashlib.sha256(raw).hexdigest() == sha:
                break
            if attempt:
                # Duas leituras completas e o conteúdo não bate com o sha do cabeçalho: a lista foi
                # alterada na flash depois de gravada (no dimmer, a memória dos níveis grava no mesmo
                # lugar). Fica ignorada até ser gravada de novo (o sha do cabeçalho muda).
                raise VoiceFormatError("o conteúdo do slot 6 não confere com o sha256 gravado")
        try:
            return parse_voice(raw, sha)
        except (UnicodeDecodeError, ValueError) as err:
            raise VoiceFormatError("o slot 6 não tem uma lista de voz legível") from err

    async def _download(self, addr: str, total: int) -> bytes:
        """Lê o slot 6 em fatias (a fatia encolhe se o hub estiver sem memória)."""
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
        return raw

    async def hubs(self) -> list[dict[str, Any]]:
        """Todos os hubs da malha (devices.loaddevices, 25 por página)."""
        out: list[dict[str, Any]] = []
        frm = 0
        while True:
            d = (await self.request("*", "devices.loaddevices", {"from": frm, "max": 25}))["data"]
            page = d.get("list") or []
            out.extend(page)
            frm += len(page)
            if not page or frm >= int(d.get("total") or 0):
                return out


@dataclass
class Holder:
    """Um hub que guarda lista de voz."""

    sn: str
    addr: str
    host: str
    sha256: str


class NexoSystem:
    """O sistema Nexo da casa: uma conexão por hub que guarda lista, os endpoints juntos e o estado."""

    def __init__(self, holders: list[Holder], endpoints: list[Endpoint], port: int = PORT,
                 ignored: dict[str, str] | None = None) -> None:
        self.holders = holders                 # o primeiro é a âncora: os avisos da malha vêm por ele
        self.ignored = dict(ignored or {})     # SN -> sha do slot 6 que não é lista de voz (dimmer usa o espaço)
        self.endpoints: dict[str, Endpoint] = {e.uuid: e for e in endpoints}
        self.states: dict[str, dict[str, Any]] = {}
        self.on_list_changed: Callable[[], None] | None = None
        self.port = port
        self._links: dict[str, NexoLink] = {}
        self._listeners: dict[str, set[Listener]] = {}
        self._by_out: dict[tuple[str, str, int], Endpoint] = {}
        self._chores: asyncio.Task | None = None
        for e in endpoints:
            if e.kind in ("R", "D"):
                self._by_out[("o", e.addr, e.out)] = e
            elif e.kind == "B":
                self._by_out[("m", e.addr, e.out // 2)] = e

    @property
    def anchor(self) -> Holder:
        return self.holders[0]

    @property
    def connected(self) -> bool:
        link = self._links.get(self.anchor.sn)
        return bool(link and link.connected)

    def endpoint_available(self, ep: Endpoint) -> bool:
        link = self._links.get(ep.holder)
        return bool(link and link.connected)

    # ------------------------------------------------------------------ ciclo de vida
    async def start(self) -> None:
        for i, h in enumerate(self.holders):
            link = NexoLink(
                h.host, self.port,
                on_message=self._on_message if i == 0 else None,   # avisos só pela âncora (chegam a toda conexão)
                on_state=lambda _s: self._notify_all(),
            )
            self._links[h.sn] = link
            if i == 0:
                try:
                    await link.start()
                except ConnectionError:
                    await self.stop()
                    raise
            else:
                # outro hub com lista: se estiver fora do ar, só os itens dele ficam indisponíveis
                await link.start(require=False)
        self._chores = asyncio.create_task(self._run_chores(), name="nexohub-rotina")

    async def stop(self) -> None:
        if self._chores:
            self._chores.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._chores
            self._chores = None
        for link in self._links.values():
            await link.stop()

    async def _run_chores(self) -> None:
        rounds = 0
        while True:
            with contextlib.suppress(ConnectionError, NexoError, OSError):
                if rounds and rounds % SCAN_EVERY == 0 and await self.lists_changed():
                    _LOGGER.info("Nexo: a lista de voz de algum hub mudou")
                    if self.on_list_changed:
                        self.on_list_changed()
                await self.report_all()
            rounds += 1
            await asyncio.sleep(REPORT_EVERY_S)

    async def lists_changed(self) -> bool:
        """Algum hub ganhou, perdeu ou mudou a lista de voz desde a carga."""
        anchor = self._links[self.anchor.sn]
        known = {h.sn: h.sha256 for h in self.holders}
        for hub in await anchor.hubs():
            sn, addr = str(hub.get("sn") or ""), str(hub.get("addr") or "").upper()
            if not sn or not addr or not hub.get("on", True):
                continue   # hub fora do ar não conta como mudança
            try:
                total, sha = await anchor.voice_head(addr)
            except NexoError:
                continue
            if sn in known:
                if sha != known[sn]:
                    return True           # mudou ou foi apagada
            elif total and self.ignored.get(sn) != sha:
                return True               # hub que não tinha lista agora tem
        return False

    # ------------------------------------------------------------------ comando e estado
    async def command(self, ep: Endpoint, state: dict[str, Any]) -> None:
        """Comando pelo endpoint, como a voz chega, no hub que guarda a lista dele. O hub sempre responde ok."""
        link = self._links.get(ep.holder)
        if link is None:
            raise ConnectionError(f"sem conexão com o hub {ep.holder}")
        rid = await link.send({"source": "Alexa", "endpoint_id": ep.endpoint_id, "state": state})
        with contextlib.suppress(NexoError):
            await link.wait(rid)

    async def report(self, ep: Endpoint) -> dict[str, Any] | None:
        """Estado do endpoint na memória do hub da lista (None = desconhecido)."""
        link = self._links.get(ep.holder)
        if link is None:
            raise ConnectionError(f"sem conexão com o hub {ep.holder}")
        reply = await link.wait(await link.send({"source": "Alexa", "report": "state", "endpoint_id": ep.endpoint_id}))
        st = state_from_report(ep.kind, reply.get("state"))
        if st is not None:
            self._set_state(ep, st)
        return st

    async def report_all(self) -> None:
        for ep in list(self.endpoints.values()):
            if ep.kind != "S" and self.endpoint_available(ep):
                with contextlib.suppress(NexoError, ConnectionError):
                    await self.report(ep)

    # ------------------------------------------------------------------ avisos da malha
    def _on_message(self, msg: dict) -> None:
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
        """Avisa cb quando muda o endpoint `key` (UUID) ou qualquer conexão."""
        self._listeners.setdefault(key, set()).add(cb)

        def remove() -> None:
            self._listeners.get(key, set()).discard(cb)

        return remove

    def _notify(self, key: str) -> None:
        for cb in list(self._listeners.get(key, ())):
            cb()

    def _notify_all(self) -> None:
        for cbs in list(self._listeners.values()):
            for cb in list(cbs):
                cb()
