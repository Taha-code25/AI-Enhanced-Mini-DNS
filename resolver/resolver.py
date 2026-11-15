
"""
resolver.py - UDP resolver with TTL cache and upstream retries.
Run from the project root with:
    python -m resolver.resolver
"""
import threading
import argparse
from contextlib import nullcontext
import logging
from pathlib import Path
import socket
import time

from logging_system.query_log import QueryLog
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


def ask_authoritative(domain, config, request_ids, log_event=None):
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
                if log_event is not None:
                    log_event("UPSTREAM_SENT", upstream_request_id=upstream_id,
                              udp_payload_bytes=len(packet))
                data, sender = sock.recvfrom(1201)
                if log_event is not None:
                    log_event("UPSTREAM_RECEIVED", upstream_request_id=upstream_id,
                              udp_payload_bytes=len(data))

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
                if log_event is not None:
                    log_event("UPSTREAM_TIMEOUT", upstream_request_id=upstream_id)
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


def handle_query(data, client_address, sock, config, cache, request_ids, query_log=None):
    """Validate one client datagram and send a response."""
    start = time.perf_counter()
    request_id = peek_request_id(data)
    domain = None
    policy = cache.policy

    def log_event(event, **details):
        if query_log is not None:
            query_log.write(event, request_id, domain, policy, **details)

    def send_reply(packet, status, ttl=None):
        sock.sendto(packet, client_address)
        log_event("RESPONSE_SENT" if status == "OK" else "ERROR_SENT",
                  status=status, ttl=ttl, udp_payload_bytes=len(packet),
                  latency_ms=(time.perf_counter() - start) * 1000)

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
        if exc.request_id is not None:
            request_id = exc.request_id
        log_event("QUERY_REJECTED", udp_payload_bytes=len(data), status=exc.error_code.name)
        try:
            send_reply(encode_error(request_id, exc.error_code), exc.error_code.name)
        except (ProtocolError, OSError):
            logger.warning("Could not send protocol error to %s", client_address)
        return

    request_id = message.request_id
    log_event("QUERY_RECEIVED", udp_payload_bytes=len(data))

    cached = cache.get(domain)
    if cached is not None:
        ip, ttl = cached
        logger.info("CACHE_HIT domain=%s ttl=%s", domain, ttl)
        log_event("CACHE_HIT", ttl=ttl)
        send_reply(encode_response(request_id, ip, ttl), "OK", ttl)
        return

    logger.info("CACHE_MISS domain=%s", domain)
    log_event("CACHE_MISS")
    answer = ask_authoritative(domain, config, request_ids, log_event)

    if answer == ErrorCode.NOT_FOUND:
        send_reply(encode_error(request_id, ErrorCode.NOT_FOUND), "NOT_FOUND")
        return

    if answer is None:
        send_reply(encode_error(request_id, ErrorCode.SERVFAIL), "SERVFAIL")
        return

    ip, ttl = answer
    cache.put(domain, ip, ttl)

    send_reply(encode_response(request_id, ip, ttl), "OK", ttl)
    logger.info("RESOLVED domain=%s ip=%s ttl=%s", domain, ip, ttl)

class ResolverServer:
    """Stoppable UDP resolver server for GUI and programmatic use."""

    def __init__(self, config, cache, query_log_path=None):
        self.config = config
        self.cache = cache
        self.query_log_path = query_log_path

        self.request_ids = RequestIdGenerator()
        self.sock = None
        self.thread = None
        self.running = threading.Event()
        self.address = None

    def start(self):
        """Start the UDP resolver in a background thread."""
        if self.running.is_set():
            return

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(0.25)

        try:
            self.sock.bind(
                (self.config.resolver_host, self.config.resolver_port)
            )
        except OSError:
            self.sock.close()
            self.sock = None
            raise

        self.address = self.sock.getsockname()
        self.running.set()

        self.thread = threading.Thread(
            target=self._serve,
            name="ResolverServer",
            daemon=True,
        )
        self.thread.start()

        logger.info(
            "Resolver listening on %s:%s", *self.address
        )

    def _serve(self):
        """Receive and process DNS requests until stopped."""
        log_context = (
            QueryLog(self.query_log_path)
            if self.query_log_path else nullcontext()
        )

        try:
            with log_context as query_log:
                while self.running.is_set():
                    try:
                        data, client_address = self.sock.recvfrom(1201)

                        handle_query(
                            data,
                            client_address,
                            self.sock,
                            self.config,
                            self.cache,
                            self.request_ids,
                            query_log,
                        )

                    except socket.timeout:
                        continue

                    except OSError:
                        if self.running.is_set():
                            logger.exception("Resolver socket error")
                        break

        except Exception:
            logger.exception("Resolver server stopped unexpectedly")

        finally:
            self.running.clear()

    def stop(self):
        """Stop the UDP resolver safely."""
        self.running.clear()

        if self.sock is not None:
            self.sock.close()
            self.sock = None

        if self.thread is not None:
            self.thread.join(timeout=2)
            self.thread = None

        self.address = None
        logger.info("Resolver stopped")



def main():
    parser = argparse.ArgumentParser(description="Mini DNS resolver")
    parser.add_argument(
        "--query-log",
        type=Path,
        help="append resolver events to a CSV file",
    )
    parser.add_argument(
        "--ai-model",
        type=Path,
        help="use a locally trained Random Forest",
    )
    args = parser.parse_args()

    config = load_config()

    if args.ai_model is None:
        cache = DNSCache(config.cache_capacity)
    else:
        from ai.cache_policy import PredictiveCache

        try:
            cache = PredictiveCache.from_file(
                config.cache_capacity,
                args.ai_model,
            )
        except (OSError, ValueError, ImportError) as exc:
            parser.error(str(exc))

    server = ResolverServer(
        config=config,
        cache=cache,
        query_log_path=args.query_log,
    )

    try:
        server.start()

        while server.running.is_set():
            time.sleep(0.5)

    except KeyboardInterrupt:
        logger.info("Resolver shutting down")

    finally:
        server.stop()


if __name__ == "__main__":
    main()
