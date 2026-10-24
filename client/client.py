"""
client.py - Client for the AI-Enhanced Mini DNS (Member 1).

Sends a QUERY to the resolver over UDP, matches the reply by request ID and
sender, and prints the IP + TTL or a readable error. Follows protocol.md:
- domain normalised/validated locally before sending (section 5.1)
- retries reuse the SAME request ID (sections 9, 10.5)
- replies from the wrong sender / wrong ID / wrong type are ignored

Usage (from the repo root):
    python client/client.py portal.university.test
    python client/client.py a.test b.test --repeat 5 --log results/client_log.csv
    python client/client.py                      # interactive prompt
    python client/client.py x.test --host 192.168.1.20 --port 5300
"""
from __future__ import annotations

import argparse
import csv
import socket
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Make protocol/protocol.py importable no matter where the script is run from.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "protocol"))
from protocol import (  # noqa: E402
    ErrorCode, MAX_DATAGRAM_SIZE, MessageType, ProtocolError, RequestIdGenerator,
    decode_error_payload, decode_response_payload, encode_query, is_expected_reply,
    load_config, normalize_domain,
)

ERROR_TEXT = {
    ErrorCode.BAD_REQUEST: "The resolver rejected the query as invalid.",
    ErrorCode.NOT_FOUND: "Domain not found (no such record).",
    ErrorCode.SERVFAIL: "Resolver could not complete resolution (upstream failure/timeout).",
    ErrorCode.UNSUPPORTED_VERSION: "Protocol version not supported by the server.",
    ErrorCode.UNSUPPORTED_TYPE: "Message type not supported by the server.",
    ErrorCode.TOO_LARGE: "Message exceeded the datagram size limit.",
    ErrorCode.INTERNAL_ERROR: "Internal server error.",
}


@dataclass
class LookupResult:
    domain: str
    request_id: Optional[int]
    ok: bool
    ip: Optional[str] = None
    ttl: Optional[int] = None
    error: Optional[str] = None     # human-readable
    error_name: Optional[str] = None  # e.g. NOT_FOUND, LOCAL_TIMEOUT, INVALID_DOMAIN
    latency_ms: float = 0.0
    attempts: int = 0


class DnsClient:
    def __init__(self, host: str, port: int, timeout: float, retries: int):
        self.server = (host, port)
        self.timeout = timeout
        self.retries = retries
        self.ids = RequestIdGenerator()

    def lookup(self, domain: str) -> LookupResult:
        # 1. Normalise + validate locally (no network traffic for bad input).
        try:
            packet_domain = normalize_domain(domain)
            request_id = self.ids.next_id()
            packet = encode_query(request_id, packet_domain)
        except ProtocolError as exc:
            return LookupResult(domain, None, False, error=f"Invalid domain: {exc}",
                                error_name="INVALID_DOMAIN")

        start = time.perf_counter()
        attempts = 1 + self.retries
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            for attempt in range(1, attempts + 1):
                try:
                    sock.sendto(packet, self.server)   # retry reuses same packet/ID
                except OSError as exc:
                    return LookupResult(packet_domain, request_id, False,
                                        error=f"Network error: {exc}", error_name="NETWORK_ERROR",
                                        latency_ms=_ms(start), attempts=attempt)
                reply = self._wait_for_reply(sock, request_id)
                if reply is not None:
                    return self._interpret(reply, packet_domain, request_id, start, attempt)
            # every attempt timed out
            return LookupResult(packet_domain, request_id, False,
                                error=f"No reply after {attempts} attempt(s) "
                                      f"({self.timeout}s each).",
                                error_name="LOCAL_TIMEOUT", latency_ms=_ms(start),
                                attempts=attempts)

    def _wait_for_reply(self, sock: socket.socket, request_id: int):
        """Wait up to self.timeout for ONE valid reply; ignore everything else."""
        deadline = time.monotonic() + self.timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            sock.settimeout(remaining)
            try:
                data, sender = sock.recvfrom(MAX_DATAGRAM_SIZE + 1)
            except socket.timeout:
                return None
            except ConnectionResetError:     # Windows: ICMP port-unreachable
                time.sleep(min(0.2, max(0, deadline - time.monotonic())))
                continue
            msg = is_expected_reply(data, sender, self.server, request_id)
            if msg is not None:
                return msg
            # else: late/duplicate/spoofed/mismatched -> ignore, keep waiting

    def _interpret(self, msg, domain, request_id, start, attempt) -> LookupResult:
        latency = _ms(start)
        try:
            if msg.message_type == MessageType.RESPONSE:
                data = decode_response_payload(msg.payload)
                return LookupResult(domain, request_id, True, ip=data.ip, ttl=data.ttl,
                                    latency_ms=latency, attempts=attempt)
            err = decode_error_payload(msg.payload)
            return LookupResult(domain, request_id, False,
                                error=ERROR_TEXT.get(err.error_code, "Unknown error."),
                                error_name=err.error_code.name, latency_ms=latency,
                                attempts=attempt)
        except ProtocolError as exc:
            return LookupResult(domain, request_id, False,
                                error=f"Malformed reply from resolver: {exc}",
                                error_name="MALFORMED_REPLY", latency_ms=latency,
                                attempts=attempt)


def _ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000.0


def show(r: LookupResult) -> None:
    if r.ok:
        print(f"{r.domain} -> {r.ip}  (TTL {r.ttl}s, {r.latency_ms:.2f} ms, "
              f"attempts={r.attempts})")
    else:
        print(f"{r.domain or '?'} -> ERROR [{r.error_name}] {r.error} "
              f"({r.latency_ms:.2f} ms)")


def append_log(path: str, r: LookupResult) -> None:
    """One CSV row per lookup, using the field names from protocol.md section 13."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    new = not p.exists()
    with p.open("a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["timestamp", "component", "request_id", "domain", "event",
                        "latency_ms", "ttl", "cache_policy"])
        w.writerow([time.time(), "client", r.request_id, r.domain,
                    "RESPONSE_RECEIVED" if r.ok else (r.error_name or "ERROR"),
                    f"{r.latency_ms:.3f}", r.ttl if r.ttl is not None else "", ""])


def main() -> int:
    cfg = load_config()
    ap = argparse.ArgumentParser(description="Mini DNS client")
    ap.add_argument("domains", nargs="*", help="domain(s) to resolve; omit for interactive mode")
    ap.add_argument("--host", default=cfg.resolver_host, help="resolver IP (default: env/127.0.0.1)")
    ap.add_argument("--port", type=int, default=cfg.resolver_port)
    ap.add_argument("--timeout", type=float, default=cfg.client_timeout)
    ap.add_argument("--retries", type=int, default=cfg.client_retries)
    ap.add_argument("--repeat", type=int, default=1, help="repeat the whole list N times")
    ap.add_argument("--delay", type=float, default=0.0, help="seconds between queries")
    ap.add_argument("--log", help="append results to this CSV file")
    args = ap.parse_args()

    client = DnsClient(args.host, args.port, args.timeout, args.retries)

    def run(domain: str) -> LookupResult:
        r = client.lookup(domain)
        show(r)
        if args.log:
            append_log(args.log, r)
        return r

    if args.domains:
        failures = 0
        for _ in range(args.repeat):
            for d in args.domains:
                failures += not run(d).ok
                if args.delay:
                    time.sleep(args.delay)
        return 1 if failures else 0

    print(f"Mini DNS client -> resolver {args.host}:{args.port}. Type a domain, or 'quit'.")
    while True:
        try:
            line = input("domain> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if line.lower() in ("quit", "exit", "q"):
            return 0
        if line:
            run(line)


if __name__ == "__main__":
    sys.exit(main())
