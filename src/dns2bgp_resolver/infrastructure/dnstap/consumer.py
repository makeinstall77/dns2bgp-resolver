from __future__ import annotations

"""Minimal Frame Streams (fstrm) bidirectional reader for dnstap."""

import asyncio
import logging
import struct
from collections.abc import Awaitable, Callable
from pathlib import Path

logger = logging.getLogger(__name__)

_CONTENT_TYPE = b"protobuf:dnstap.Dnstap"

# Control frame types
_CONTROL_ACCEPT = 0x01
_CONTROL_START = 0x02
_CONTROL_STOP = 0x03
_CONTROL_READY = 0x04
_CONTROL_FINISH = 0x05

_FIELD_CONTENT_TYPE = 0x01


def _encode_control(control_type: int, content_types: list[bytes] | None = None) -> bytes:
    parts = [struct.pack("!I", control_type)]
    if content_types:
        for ct in content_types:
            parts.append(struct.pack("!I", _FIELD_CONTENT_TYPE))
            parts.append(struct.pack("!I", len(ct)))
            parts.append(ct)
    body = b"".join(parts)
    # control frame: 0 length escape + control length + body
    return struct.pack("!I", 0) + struct.pack("!I", len(body)) + body


def _decode_control(body: bytes) -> tuple[int, list[bytes]]:
    if len(body) < 4:
        raise ValueError("short control frame")
    (ctype,) = struct.unpack("!I", body[:4])
    types: list[bytes] = []
    i = 4
    while i + 8 <= len(body):
        (field_type, field_len) = struct.unpack("!II", body[i : i + 8])
        i += 8
        if i + field_len > len(body):
            break
        value = body[i : i + field_len]
        i += field_len
        if field_type == _FIELD_CONTENT_TYPE:
            types.append(value)
    return ctype, types


async def _read_exact(reader: asyncio.StreamReader, n: int) -> bytes:
    data = await reader.readexactly(n)
    return data


async def _read_frame(reader: asyncio.StreamReader) -> tuple[bool, bytes]:
    """Return (is_control, payload)."""
    header = await _read_exact(reader, 4)
    (length,) = struct.unpack("!I", header)
    if length == 0:
        ctrl_len_b = await _read_exact(reader, 4)
        (ctrl_len,) = struct.unpack("!I", ctrl_len_b)
        body = await _read_exact(reader, ctrl_len) if ctrl_len else b""
        return True, body
    payload = await _read_exact(reader, length)
    return False, payload


async def handshake_as_receiver(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """fstrm handshake: uni (START) or bi (READY → ACCEPT → START)."""
    is_ctrl, body = await _read_frame(reader)
    if not is_ctrl:
        raise ValueError("expected control frame at start of stream")
    ctype, types = _decode_control(body)
    if ctype == _CONTROL_START:
        return
    if ctype != _CONTROL_READY:
        raise ValueError(f"expected READY or START, got {ctype}")
    if types and _CONTENT_TYPE not in types:
        logger.warning("READY content-types=%s", types)

    writer.write(_encode_control(_CONTROL_ACCEPT, [_CONTENT_TYPE]))
    await writer.drain()

    is_ctrl, body = await _read_frame(reader)
    if not is_ctrl:
        raise ValueError("expected START control frame")
    ctype, _ = _decode_control(body)
    if ctype != _CONTROL_START:
        raise ValueError(f"expected START, got {ctype}")


def _decode_varint(buf: bytes, i: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while i < len(buf):
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, i
        shift += 7
        if shift > 63:
            raise ValueError("varint too long")
    raise ValueError("truncated varint")


def extract_dnstap_response_wire(dnstap_bytes: bytes) -> bytes | None:
    """
    Pull DNS response wire from dnstap.Dnstap → Message.

    Nested Message is field 14. Prefer response_message (15); Unbound often
    puts CLIENT_RESPONSE wire in query_message (14).
    """
    i = 0
    message_bytes: bytes | None = None
    while i < len(dnstap_bytes):
        tag, i = _decode_varint(dnstap_bytes, i)
        field_no = tag >> 3
        wire_type = tag & 0x7
        if wire_type == 0:  # varint
            _, i = _decode_varint(dnstap_bytes, i)
        elif wire_type == 1:  # 64-bit
            i += 8
        elif wire_type == 2:  # length-delimited
            length, i = _decode_varint(dnstap_bytes, i)
            chunk = dnstap_bytes[i : i + length]
            i += length
            if field_no == 14:
                message_bytes = chunk
        elif wire_type == 5:  # 32-bit
            i += 4
        else:
            break
    if message_bytes is None:
        return None

    i = 0
    response: bytes | None = None
    query_msg: bytes | None = None
    msg_type: int | None = None
    while i < len(message_bytes):
        tag, i = _decode_varint(message_bytes, i)
        field_no = tag >> 3
        wire_type = tag & 0x7
        if wire_type == 0:
            val, i = _decode_varint(message_bytes, i)
            if field_no == 1:
                msg_type = val
        elif wire_type == 1:
            i += 8
        elif wire_type == 2:
            length, i = _decode_varint(message_bytes, i)
            chunk = message_bytes[i : i + length]
            i += length
            if field_no == 14:
                query_msg = chunk
            elif field_no == 15:
                response = chunk
        elif wire_type == 5:
            i += 4
        else:
            break

    # CLIENT_RESPONSE=6, RESOLVER_RESPONSE=4, AUTH_RESPONSE=2, FORWARDER_RESPONSE=8, STUB_RESPONSE=10
    if msg_type is not None and msg_type not in (2, 4, 6, 8, 10):
        return None
    # Unbound/protobuf-c often packs the DNS reply into field 14 on CLIENT_RESPONSE.
    return response or query_msg


OnDnsEvent = Callable[[str, list[str]], Awaitable[None]]


class DnstapUnixServer:
    """Listen for unbound dnstap on unix and/or TCP; stream fstrm frames."""

    def __init__(
        self,
        path: str,
        on_response: OnDnsEvent,
        *,
        socket_mode: int = 0o666,
        listen_tcp: str = "",
    ) -> None:
        self._path = path.strip()
        self._listen_tcp = listen_tcp.strip()
        self._on_response = on_response
        self._socket_mode = socket_mode
        self._servers: list[asyncio.AbstractServer] = []
        self._stop = asyncio.Event()

    async def start(self) -> None:
        import os

        if not self._path and not self._listen_tcp:
            raise ValueError("dnstap requires listen_unix and/or listen_tcp")

        self._stop.clear()
        self._servers.clear()

        if self._path:
            path = Path(self._path)
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                path.unlink()
            # umask 022 → socket 0755 before chmod; unbound needs write to connect (EACCES).
            old_umask = os.umask(0)
            try:
                server = await asyncio.start_unix_server(
                    self._handle_client, path=str(path)
                )
                path.chmod(self._socket_mode)
            finally:
                os.umask(old_umask)
            self._servers.append(server)
            logger.info("dnstap listening on unix %s", path)

        if self._listen_tcp:
            host, _, port_s = self._listen_tcp.rpartition(":")
            if not host or not port_s:
                raise ValueError(f"invalid dnstap.listen_tcp: {self._listen_tcp!r}")
            server = await asyncio.start_server(
                self._handle_client, host=host, port=int(port_s)
            )
            self._servers.append(server)
            logger.info("dnstap listening on tcp %s", self._listen_tcp)

    async def stop(self) -> None:
        self._stop.set()
        for server in self._servers:
            server.close()
            await server.wait_closed()
        self._servers.clear()
        if self._path:
            path = Path(self._path)
            if path.exists():
                try:
                    path.unlink()
                except OSError:
                    pass
        logger.info("dnstap stopped")

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peer = writer.get_extra_info("peername")
        logger.debug("dnstap client connected: %s", peer)
        try:
            await handshake_as_receiver(reader, writer)
            while not self._stop.is_set():
                try:
                    is_ctrl, payload = await _read_frame(reader)
                except asyncio.IncompleteReadError:
                    break
                if is_ctrl:
                    ctype, _ = _decode_control(payload) if payload else (0, [])
                    if ctype in (_CONTROL_STOP, _CONTROL_FINISH):
                        break
                    continue
                await self._dispatch_frame(payload)
        except Exception:  # noqa: BLE001
            logger.exception("dnstap client error")
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass
            logger.debug("dnstap client disconnected")

    async def _dispatch_frame(self, dnstap_bytes: bytes) -> None:
        import dns.message
        import dns.rdatatype

        wire = extract_dnstap_response_wire(dnstap_bytes)
        if not wire:
            return
        try:
            msg = dns.message.from_wire(wire)
        except Exception:  # noqa: BLE001
            logger.debug("dnstap drop: dns parse failed", exc_info=True)
            return
        if not msg.question:
            return
        qname = str(msg.question[0].name).rstrip(".").lower()
        ips: list[str] = []
        for rrset in msg.answer:
            if rrset.rdtype != dns.rdatatype.A:
                continue
            for rdata in rrset:
                ips.append(rdata.address)
        if not ips:
            return
        logger.debug("dnstap q=%s ips=%s", qname, ips)
        await self._on_response(qname, ips)
