
"""
resolver.py - UDP resolver with TTL cache and upstream retries.
Run from the project root with:
    python -m resolver.resolver
"""

import logging
import socket

from protocol.protocol import (
    ErrorCode,
    MessageType,
    ProtocolError,
    RequestIdGenerator,
    decode_message,
    decode_query_payload,
    decode_response_payload,
    encode_error,
    encode_response,
    is_expected_reply,
    load_config,
    peek_request_id,
)
from resolver.cache import DNSCache


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("resolver")


def make_upstream_request(domain, request_id):
    """Build an upstream QUERY using the shared protocol encoder."""
    from protocol.protocol import encode_query
    return encode_query(request_id, domain)


def ask_authoritative(domain, config, request_ids):
    """
    Ask the authoritative server for a domain.
    Returns (IP, TTL) on success, or None if all attempts fail.
    """
    upstream_id = request_ids.next_id()

    packet = make_upstream_request(domain, upstream_id)
    expected_server = (config.auth_host, config.auth_port)

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(config.upstream_timeout)

        for attempt in range(config.upstream_retries + 1):
            try:
                # Retries reuse the same packet and request ID.
                sock.sendto(packet, expected_server)
                data, sender = sock.recvfrom(1201)

                reply = is_expected_reply(
                    data, sender, expected_server, upstream_id
                )

                if reply is None:
                    logger.warning(
                        "Ignoring unexpected upstream reply for %s", domain
                    )
                    continue

                if reply.message_type == MessageType.ERROR:
                    from protocol.protocol import decode_error_payload
                    error = decode_error_payload(reply.payload)
                    if error.error_code == ErrorCode.NOT_FOUND:
                        return ErrorCode.NOT_FOUND
                    logger.warning(
                        "Upstream error for %s: %s",
                        domain, error.error_code.name
                    )
                    return None

                result = decode_response_payload(reply.payload)
                return result.ip, result.ttl

            except socket.timeout:
                logger.warning(
                    "Upstream timeout for %s (attempt %s/%s)",
                    domain, attempt + 1, config.upstream_retries + 1
                )
            except OSError as exc:
                logger.warning(
                    "Upstream socket error for %s (attempt %s/%s): %s",
                     domain,
                     attempt + 1,
                    config.upstream_retries + 1,
                    exc,
                )
                continue
            except ProtocolError as exc:
                logger.warning(
                     "Invalid upstream response for %s: %s", domain, exc
                )  
                continue


    return None


def handle_query(data, client_address, sock, config, cache, request_ids):
    """Validate one client datagram and send a response."""
    request_id = peek_request_id(data)

    try:
        message = decode_message(data)

        if message.message_type != MessageType.QUERY:
            raise ProtocolError(
                "resolver expects QUERY messages",
                ErrorCode.UNSUPPORTED_TYPE,
                message.request_id,
            )

        domain = decode_query_payload(message.payload)

    except ProtocolError as exc:
        try:
            sock.sendto(
                encode_error(
                    exc.request_id if exc.request_id is not None else request_id,
                    exc.error_code,
                ),
                client_address,
            )
        except (ProtocolError, OSError):
            logger.warning("Could not send protocol error to %s", client_address)
        return

    request_id = message.request_id

    cached = cache.get(domain)
    if cached is not None:
        ip, ttl = cached
        logger.info("CACHE_HIT domain=%s ttl=%s", domain, ttl)
        sock.sendto(encode_response(request_id, ip, ttl), client_address)
        return

    logger.info("CACHE_MISS domain=%s", domain)
    answer = ask_authoritative(domain, config, request_ids)

    if answer == ErrorCode.NOT_FOUND:
        sock.sendto(
            encode_error(request_id, ErrorCode.NOT_FOUND), client_address
        )
        return

    if answer is None:
        sock.sendto(
            encode_error(request_id, ErrorCode.SERVFAIL), client_address
        )
        return

    ip, ttl = answer
    cache.put(domain, ip, ttl)

    sock.sendto(encode_response(request_id, ip, ttl), client_address)
    logger.info("RESOLVED domain=%s ip=%s ttl=%s", domain, ip, ttl)


def main():
    config = load_config()
    cache = DNSCache(config.cache_capacity)
    request_ids = RequestIdGenerator()

    listen_address = (config.resolver_host, config.resolver_port)

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(listen_address)
        logger.info("Resolver listening on %s:%s", *listen_address)

        while True:
            try:
                data, client_address = sock.recvfrom(1201)
                handle_query(
                    data, client_address, sock, config, cache, request_ids
                )
            except KeyboardInterrupt:
                logger.info("Resolver shutting down")
                break
            except OSError as exc:
                logger.exception("Socket error: %s", exc)


if __name__ == "__main__":
    main()
