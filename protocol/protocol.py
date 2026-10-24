"""
protocol.py - Shared protocol module for the AI-Enhanced Mini DNS project.

Implements the encoders, decoders, constants, domain normalization and
validation helpers described in protocol.md (version 1). The client, resolver
and authoritative server should all import this module so every component
agrees on the wire format.

This is a custom educational protocol, NOT the public DNS wire protocol.
"""

from __future__ import annotations

import ipaddress
import os
import re
import struct
import time
from dataclasses import dataclass
from enum import IntEnum
from typing import Optional, Tuple, Union

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

PROTOCOL_VERSION = 1

HEADER_FORMAT = "!BBIH"          # version, message_type, request_id, payload_length
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)  # 8 bytes

MAX_DATAGRAM_SIZE = 1200         # project safety limit (whole datagram)
MAX_PAYLOAD_SIZE = MAX_DATAGRAM_SIZE - HEADER_SIZE

RESPONSE_FORMAT = "!BIB4s"       # status, ttl, address_family, ipv4 (packed)
RESPONSE_PAYLOAD_SIZE = struct.calcsize(RESPONSE_FORMAT)  # 10 bytes

ERROR_FORMAT = "!BB"             # error_code, detail_code
ERROR_PAYLOAD_SIZE = struct.calcsize(ERROR_FORMAT)        # 2 bytes

STATUS_OK = 0x00
ADDRESS_FAMILY_IPV4 = 0x04

MAX_REQUEST_ID = 2**32 - 1
MAX_TTL = 2**32 - 1

MAX_DOMAIN_LENGTH = 253
MAX_LABEL_LENGTH = 63
_LABEL_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")


class MessageType(IntEnum):
    QUERY = 0x01
    RESPONSE = 0x02
    ERROR = 0x03
    HEALTH_CHECK = 0x04      # optional
    HEALTH_RESPONSE = 0x05   # optional


class ErrorCode(IntEnum):
    BAD_REQUEST = 0x01
    NOT_FOUND = 0x02
    SERVFAIL = 0x03
    UNSUPPORTED_VERSION = 0x04
    UNSUPPORTED_TYPE = 0x05
    TOO_LARGE = 0x06
    INTERNAL_ERROR = 0x07


# --------------------------------------------------------------------------
# Exceptions
# --------------------------------------------------------------------------

class ProtocolError(Exception):
    """Raised when a message or domain violates the protocol.

    ``error_code`` is the ErrorCode a server should report (if it chooses to
    reply). ``request_id`` is set when the header was parseable, else None.
    """

    def __init__(self, message: str, error_code: ErrorCode = ErrorCode.BAD_REQUEST,
                 request_id: Optional[int] = None):
        super().__init__(message)
        self.error_code = ErrorCode(error_code)
        self.request_id = request_id


# --------------------------------------------------------------------------
# Domain normalization and validation (protocol.md section 5.1)
# --------------------------------------------------------------------------

def normalize_domain(name: str) -> str:
    """Normalize and validate a user-entered domain name.

    Steps: strip whitespace -> lowercase ASCII -> remove ONE trailing dot ->
    validate. Returns the normalized name or raises ProtocolError(BAD_REQUEST).
    """
    if not isinstance(name, str):
        raise ProtocolError("domain must be a string")

    name = name.strip()
    if not name.isascii():
        raise ProtocolError("domain contains non-ASCII characters")
    name = name.lower()
    if name.endswith("."):
        name = name[:-1]

    validate_domain(name)
    return name


def validate_domain(name: str) -> None:
    """Validate an ALREADY-normalized domain. Raises ProtocolError if invalid."""
    if not 1 <= len(name) <= MAX_DOMAIN_LENGTH:
        raise ProtocolError(f"domain length must be 1-{MAX_DOMAIN_LENGTH}")
    for label in name.split("."):
        if not 1 <= len(label) <= MAX_LABEL_LENGTH:
            raise ProtocolError(f"label length must be 1-{MAX_LABEL_LENGTH}")
        if not _LABEL_RE.fullmatch(label):
            raise ProtocolError("labels may contain only a-z, 0-9 and "
                                "hyphens, and may not start/end with a hyphen")
    if _looks_like_ip(name):
        raise ProtocolError("IP literals are not accepted as domain queries")


def _looks_like_ip(name: str) -> bool:
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        return False


# --------------------------------------------------------------------------
# Generic framing (header + payload)
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Message:
    version: int
    message_type: int
    request_id: int
    payload: bytes


def encode_message(message_type: int, request_id: int, payload: bytes = b"",
                   version: int = PROTOCOL_VERSION) -> bytes:
    """Build a complete datagram: 8-byte header + payload."""
    if not 0 <= request_id <= MAX_REQUEST_ID:
        raise ProtocolError("request_id out of range")
    if len(payload) > MAX_PAYLOAD_SIZE:
        raise ProtocolError("message exceeds datagram limit", ErrorCode.TOO_LARGE)
    header = struct.pack(HEADER_FORMAT, version, int(message_type),
                         request_id, len(payload))
    return header + payload


def peek_request_id(data: bytes) -> int:
    """Best-effort request ID extraction; returns 0 if header is incomplete."""
    if len(data) < 6:
        return 0
    return struct.unpack_from("!I", data, 2)[0]


def decode_message(data: bytes) -> Message:
    """Strictly parse and validate a datagram.

    Rejects: oversize datagrams, short headers, wrong version, unknown type,
    short payload, and trailing bytes.
    """
    rid = peek_request_id(data)
    if len(data) > MAX_DATAGRAM_SIZE:
        raise ProtocolError("datagram too large", ErrorCode.TOO_LARGE, rid)
    if len(data) < HEADER_SIZE:
        raise ProtocolError("datagram shorter than header",
                            ErrorCode.BAD_REQUEST, rid if len(data) >= 6 else None)

    version, mtype, request_id, payload_length = struct.unpack_from(HEADER_FORMAT, data)

    if version != PROTOCOL_VERSION:
        raise ProtocolError(f"unsupported version {version}",
                            ErrorCode.UNSUPPORTED_VERSION, request_id)
    if mtype not in iter(MessageType):
        raise ProtocolError(f"unsupported message type {mtype:#04x}",
                            ErrorCode.UNSUPPORTED_TYPE, request_id)
    if len(data) < HEADER_SIZE + payload_length:
        raise ProtocolError("datagram shorter than declared payload",
                            ErrorCode.BAD_REQUEST, request_id)
    if len(data) > HEADER_SIZE + payload_length:
        raise ProtocolError("unexpected trailing bytes",
                            ErrorCode.BAD_REQUEST, request_id)

    return Message(version, mtype, request_id, data[HEADER_SIZE:])


# --------------------------------------------------------------------------
# QUERY (0x01)
# --------------------------------------------------------------------------

def encode_query(request_id: int, domain: str) -> bytes:
    """Normalize/validate ``domain`` and build a QUERY datagram."""
    normalized = normalize_domain(domain)
    return encode_message(MessageType.QUERY, request_id, normalized.encode("ascii"))


def decode_query_payload(payload: bytes) -> str:
    """Decode a QUERY payload to a normalized domain (use as cache key)."""
    try:
        text = payload.decode("ascii")
    except UnicodeDecodeError:
        raise ProtocolError("query payload is not ASCII") from None
    return normalize_domain(text)


# --------------------------------------------------------------------------
# RESPONSE (0x02)
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class ResponseData:
    ttl: int
    ip: str


def encode_response(request_id: int, ip: Union[str, ipaddress.IPv4Address],
                    ttl: int) -> bytes:
    """Build a successful RESPONSE datagram."""
    if not isinstance(ttl, int) or isinstance(ttl, bool) or not 0 <= ttl <= MAX_TTL:
        raise ProtocolError("ttl out of range")
    try:
        packed = ipaddress.IPv4Address(ip).packed
    except ValueError:
        raise ProtocolError("invalid IPv4 address") from None
    payload = struct.pack(RESPONSE_FORMAT, STATUS_OK, ttl, ADDRESS_FAMILY_IPV4, packed)
    return encode_message(MessageType.RESPONSE, request_id, payload)


def decode_response_payload(payload: bytes) -> ResponseData:
    """Validate and decode a RESPONSE payload (length, status, family, TTL, IP)."""
    if len(payload) != RESPONSE_PAYLOAD_SIZE:
        raise ProtocolError(f"response payload must be {RESPONSE_PAYLOAD_SIZE} bytes")
    status, ttl, family, packed = struct.unpack(RESPONSE_FORMAT, payload)
    if status != STATUS_OK:
        raise ProtocolError(f"unexpected response status {status:#04x}")
    if family != ADDRESS_FAMILY_IPV4:
        raise ProtocolError(f"unsupported address family {family:#04x}")
    return ResponseData(ttl=ttl, ip=str(ipaddress.IPv4Address(packed)))


# --------------------------------------------------------------------------
# ERROR (0x03)
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class ErrorData:
    error_code: ErrorCode
    detail_code: int


def encode_error(request_id: int, error_code: ErrorCode, detail_code: int = 0) -> bytes:
    """Build an ERROR datagram. Never put stack traces/paths in the payload."""
    if not 0 <= detail_code <= 255:
        raise ProtocolError("detail_code out of range")
    payload = struct.pack(ERROR_FORMAT, int(error_code), detail_code)
    return encode_message(MessageType.ERROR, request_id, payload)


def encode_error_for(exc: ProtocolError) -> bytes:
    """Convenience: turn a ProtocolError into an ERROR datagram (ID 0 if unknown)."""
    return encode_error(exc.request_id or 0, exc.error_code)


def decode_error_payload(payload: bytes) -> ErrorData:
    if len(payload) != ERROR_PAYLOAD_SIZE:
        raise ProtocolError(f"error payload must be {ERROR_PAYLOAD_SIZE} bytes")
    code, detail = struct.unpack(ERROR_FORMAT, payload)
    try:
        return ErrorData(ErrorCode(code), detail)
    except ValueError:
        raise ProtocolError(f"unknown error code {code:#04x}") from None


# --------------------------------------------------------------------------
# Optional health check (0x04 / 0x05)
# --------------------------------------------------------------------------

def encode_health_check(request_id: int) -> bytes:
    return encode_message(MessageType.HEALTH_CHECK, request_id)


def encode_health_response(request_id: int) -> bytes:
    return encode_message(MessageType.HEALTH_RESPONSE, request_id)


# --------------------------------------------------------------------------
# Request IDs and response matching (protocol.md section 9)
# --------------------------------------------------------------------------

class RequestIdGenerator:
    """Sequential 32-bit IDs starting from a random value, skipping pending IDs."""

    def __init__(self, start: Optional[int] = None):
        self._next = (start if start is not None
                      else int.from_bytes(os.urandom(4), "big")) & MAX_REQUEST_ID

    def next_id(self, pending: Optional[set] = None) -> int:
        pending = pending or set()
        for _ in range(MAX_REQUEST_ID + 1):
            candidate = self._next
            self._next = (self._next + 1) & MAX_REQUEST_ID
            if candidate not in pending:
                return candidate
        raise RuntimeError("no free request IDs")


def is_expected_reply(data: bytes, sender: Tuple[str, int],
                      expected_sender: Tuple[str, int], expected_id: int
                      ) -> Optional[Message]:
    """Return the decoded Message only if it is a valid RESPONSE/ERROR from the
    expected sender with the expected request ID; otherwise None (ignore it:
    late, duplicate, spoofed, malformed or mismatched)."""
    if sender != expected_sender:
        return None
    try:
        msg = decode_message(data)
    except ProtocolError:
        return None
    if msg.request_id != expected_id:
        return None
    if msg.message_type not in (MessageType.RESPONSE, MessageType.ERROR):
        return None
    return msg


# --------------------------------------------------------------------------
# TTL helpers (monotonic clock, protocol.md section 6)
# --------------------------------------------------------------------------

def compute_expiry(ttl: int, now: Optional[float] = None) -> float:
    """Monotonic timestamp at which an entry with ``ttl`` seconds expires."""
    return (time.monotonic() if now is None else now) + ttl


def remaining_ttl(expiry: float, now: Optional[float] = None) -> int:
    """Remaining TTL in whole seconds (rounded down). 0 or less means expired."""
    now = time.monotonic() if now is None else now
    return int(expiry - now) if expiry > now else 0


def is_expired(expiry: float, now: Optional[float] = None) -> bool:
    return remaining_ttl(expiry, now) <= 0


# --------------------------------------------------------------------------
# Configuration (protocol.md section 12)
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Config:
    resolver_host: str = "127.0.0.1"
    resolver_port: int = 5300
    auth_host: str = "127.0.0.1"
    auth_port: int = 5301
    client_timeout: float = 4.0      # seconds per attempt
    client_retries: int = 1          # retries after the first attempt
    upstream_timeout: float = 1.0    # seconds per attempt (resolver -> auth)
    upstream_retries: int = 2        # retries after the first attempt
    cache_capacity: int = 100        # max entries


def load_config() -> Config:
    """Load Config from MINIDNS_* environment variables, else defaults."""
    d = Config()
    env = os.environ.get
    return Config(
        resolver_host=env("MINIDNS_RESOLVER_HOST", d.resolver_host),
        resolver_port=int(env("MINIDNS_RESOLVER_PORT", d.resolver_port)),
        auth_host=env("MINIDNS_AUTH_HOST", d.auth_host),
        auth_port=int(env("MINIDNS_AUTH_PORT", d.auth_port)),
        client_timeout=float(env("MINIDNS_CLIENT_TIMEOUT", d.client_timeout)),
        client_retries=int(env("MINIDNS_CLIENT_RETRIES", d.client_retries)),
        upstream_timeout=float(env("MINIDNS_UPSTREAM_TIMEOUT", d.upstream_timeout)),
        upstream_retries=int(env("MINIDNS_UPSTREAM_RETRIES", d.upstream_retries)),
        cache_capacity=int(env("MINIDNS_CACHE_CAPACITY", d.cache_capacity)),
    )


# --------------------------------------------------------------------------
# Self-test:  python protocol.py
# --------------------------------------------------------------------------

def _self_test() -> None:
    # Domain normalization
    assert normalize_domain("Example.COM") == "example.com"
    assert normalize_domain("example.com.") == "example.com"
    assert normalize_domain("  sub.example.com ") == "sub.example.com"
    for bad in ["", "   ", "-bad.example", "bad-.example", "bad..example",
                "example .com", "exämple.com", "a" * 64 + ".com", "1.2.3.4",
                "example.com..", "bad\x00.com", "." ]:
        try:
            normalize_domain(bad)
        except ProtocolError:
            pass
        else:
            raise AssertionError(f"should reject {bad!r}")

    # Query round trip
    pkt = encode_query(7, "Example.COM.")
    msg = decode_message(pkt)
    assert msg.message_type == MessageType.QUERY and msg.request_id == 7
    assert decode_query_payload(msg.payload) == "example.com"

    # Response round trip
    pkt = encode_response(2**32 - 1, "93.184.216.34", 300)
    assert len(pkt) == HEADER_SIZE + RESPONSE_PAYLOAD_SIZE == 18
    msg = decode_message(pkt)
    assert decode_response_payload(msg.payload) == ResponseData(300, "93.184.216.34")

    # Error round trip
    msg = decode_message(encode_error(9, ErrorCode.NOT_FOUND))
    assert decode_error_payload(msg.payload) == ErrorData(ErrorCode.NOT_FOUND, 0)

    # Framing rejections
    good = encode_query(1, "a.com")
    for bad, code in [(good[:5], ErrorCode.BAD_REQUEST),
                      (good[:-1], ErrorCode.BAD_REQUEST),
                      (good + b"x", ErrorCode.BAD_REQUEST),
                      (b"\x02" + good[1:], ErrorCode.UNSUPPORTED_VERSION),
                      (good[:1] + b"\x63" + good[2:], ErrorCode.UNSUPPORTED_TYPE),
                      (b"\x00" * 1201, ErrorCode.TOO_LARGE)]:
        try:
            decode_message(bad)
        except ProtocolError as e:
            assert e.error_code == code, (e.error_code, code)
        else:
            raise AssertionError("should reject malformed datagram")

    # TTL helpers
    exp = compute_expiry(10, now=100.0)
    assert remaining_ttl(exp, now=103.9) == 6
    assert is_expired(exp, now=110.0)

    # Reply matching
    srv = ("127.0.0.1", 5301)
    reply = encode_response(5, "1.2.3.4", 60)
    assert is_expected_reply(reply, srv, srv, 5) is not None
    assert is_expected_reply(reply, srv, srv, 6) is None
    assert is_expected_reply(reply, ("127.0.0.1", 9), srv, 5) is None

    print("protocol.py self-test passed")


if __name__ == "__main__":
    _self_test()
