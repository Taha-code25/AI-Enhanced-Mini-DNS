"""Authoritative UDP server for the AI-Enhanced Mini DNS protocol."""

from __future__ import annotations

import argparse
import ipaddress
import json
import logging
import socket
import threading
from pathlib import Path
from typing import Optional

from protocol.protocol import (
    MAX_TTL,
    ErrorCode,
    MessageType,
    ProtocolError,
    decode_message,
    decode_query_payload,
    encode_error,
    encode_response,
    load_config,
    normalize_domain,
    peek_request_id,
)


logger = logging.getLogger("authoritative")
DEFAULT_RECORDS_PATH = Path(__file__).resolve().parent.parent / "data" / "records.json"


class RecordConfigurationError(ValueError):
    """Raised when the authoritative record file is invalid."""


def load_records(path: Optional[Path] = None) -> dict[str, tuple[str, int]]:
    """Load and validate records from JSON, independent of the working directory."""
    records_path = Path(path) if path is not None else DEFAULT_RECORDS_PATH
    try:
        with records_path.open(encoding="utf-8") as records_file:
            data = json.load(records_file)
    except (OSError, json.JSONDecodeError) as exc:
        raise RecordConfigurationError(
            f"Unable to load authoritative records from {records_path}: {exc}"
        ) from exc

    if not isinstance(data, list):
        raise RecordConfigurationError("Authoritative records must be a JSON list")

    records: dict[str, tuple[str, int]] = {}
    for index, record in enumerate(data):
        location = f"Record {index + 1}"
        if not isinstance(record, dict):
            raise RecordConfigurationError(f"{location} must be a JSON object")
        if not {"domain", "ip", "ttl"} <= record.keys():
            raise RecordConfigurationError(
                f"{location} must contain domain, ip, and ttl"
            )

        try:
            domain = normalize_domain(record["domain"])
        except (ProtocolError, TypeError) as exc:
            raise RecordConfigurationError(
                f"{location} has an invalid domain: {exc}"
            ) from exc

        if domain in records:
            raise RecordConfigurationError(f"{location} duplicates domain {domain!r}")

        ip_value = record["ip"]
        if not isinstance(ip_value, str):
            raise RecordConfigurationError(f"{location} IP address must be a string")
        try:
            ip = str(ipaddress.IPv4Address(ip_value))
        except ipaddress.AddressValueError as exc:
            raise RecordConfigurationError(
                f"{location} has an invalid IPv4 address: {ip_value!r}"
            ) from exc

        ttl = record["ttl"]
        if not isinstance(ttl, int) or isinstance(ttl, bool) or not 0 <= ttl <= MAX_TTL:
            raise RecordConfigurationError(
                f"{location} TTL must be an integer from 0 to {MAX_TTL}"
            )

        records[domain] = (ip, ttl)

    return records


def handle_datagram(
    data: bytes,
    sender: tuple[str, int],
    sock: socket.socket,
    records: dict[str, tuple[str, int]],
) -> None:
    """Validate one datagram and send its protocol response to its sender."""
    request_id = peek_request_id(data)
    try:
        message = decode_message(data)
        request_id = message.request_id
        if message.message_type != MessageType.QUERY:
            raise ProtocolError(
                "authoritative server expects QUERY messages",
                ErrorCode.UNSUPPORTED_TYPE,
                request_id,
            )

        domain = decode_query_payload(message.payload)
        answer = records.get(domain)
        if answer is None:
            response = encode_error(request_id, ErrorCode.NOT_FOUND)
        else:
            ip, ttl = answer
            response = encode_response(request_id, ip, ttl)
    except ProtocolError as exc:
        response = encode_error(
            exc.request_id if exc.request_id is not None else request_id,
            exc.error_code,
        )
    except Exception:
        logger.exception("Unexpected error while handling request from %s", sender)
        try:
            response = encode_error(request_id, ErrorCode.INTERNAL_ERROR)
        except ProtocolError:
            return

    try:
        sock.sendto(response, sender)
    except OSError:
        logger.exception("Unable to send response to %s", sender)


class AuthoritativeServer:
    """A stoppable UDP server that answers from a configured static record set."""

    def __init__(
        self,
        bind_host: str,
        port: int,
        records_path: Optional[Path] = None,
    ) -> None:
        self.records = load_records(records_path)
        self._stop_event = threading.Event()
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.settimeout(0.2)
        try:
            self._socket.bind((bind_host, port))
        except OSError:
            self._socket.close()
            raise
        self.address = self._socket.getsockname()

    def serve_forever(self) -> None:
        """Receive and process requests until shutdown is requested."""
        try:
            while not self._stop_event.is_set():
                try:
                    data, sender = self._socket.recvfrom(65535)
                except socket.timeout:
                    continue
                except OSError:
                    if not self._stop_event.is_set():
                        logger.exception("Authoritative UDP receive failed")
                    break
                handle_datagram(data, sender, self._socket, self.records)
        finally:
            self._socket.close()

    def shutdown(self) -> None:
        """Request a clean server shutdown."""
        self._stop_event.set()


def main() -> None:
    config = load_config()
    parser = argparse.ArgumentParser(description="Mini DNS authoritative server")
    parser.add_argument(
        "--bind-host",
        default=config.auth_host,
        help="address to bind (defaults to MINIDNS_AUTH_HOST or 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=config.auth_port,
        help="UDP port (defaults to MINIDNS_AUTH_PORT or 5301)",
    )
    parser.add_argument(
        "--records",
        type=Path,
        default=None,
        help="optional path to the authoritative JSON record file",
    )
    args = parser.parse_args()

    server = AuthoritativeServer(args.bind_host, args.port, args.records)
    logger.info("Authoritative server listening on %s:%s", *server.address)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Authoritative server shutting down")
        server.shutdown()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    main()
