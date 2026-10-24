# AI-Enhanced Mini DNS — Protocol Specification

**Document:** `protocol/PROTOCOL.md`  
**Version:** 1.0  
**Status:** Team proposal — agree on this specification before implementation and confirm any course-specific requirements with the instructor.

> This is a custom educational application-layer protocol, not the public DNS wire protocol. Port numbers, timeouts, message layouts, and limits below are project defaults. Keep them configurable and change them only by agreement among team members.

## 1. Purpose and Scope

This document defines how the client, caching resolver, and authoritative server communicate in the AI-Enhanced Mini DNS project.

The protocol is designed to:

- Use compact binary messages over UDP.
- Resolve configured domain names through a client → resolver → authoritative-server flow.
- Correlate requests and responses with request IDs.
- Support TTL-aware caching, an LRU baseline, and an optional AI-assisted cache policy.
- Report invalid requests, missing records, and upstream failures.
- Provide consistent information for logging and performance evaluation.

The AI component may predict which records are likely to be requested. It must **never invent or alter an IP address**, bypass validation, or serve an expired cache entry.

## 2. Architecture and Responsibilities

| Component | Responsibility |
|---|---|
| Client — Member 1 | Accept a normal text domain name, encode a query, send it to the resolver, match the response request ID, and display the IP or error. |
| Resolver and cache — Member 2 | Decode and validate packets, check cache and TTL, forward cache misses, enforce timeouts/retries, apply the selected cache policy, and return a response. |
| AI cache policy — Member 3 | Maintain query history, calculate prediction scores, and recommend cache admission/retention. It does not resolve domains or decide the correct IP. |
| Authoritative server — Member 4 | Look up names in the configured record set and return the configured IPv4 address and TTL, or a defined error. |
| Shared protocol module | Implement common encoders, decoders, constants, and validation helpers used by all network components (`protocol.py`). |

**Agreement rule:** All members must use the same header, message types, payload formats, error codes, domain normalization rules, and default configuration.

## 3. Transport and Ports

- **Transport:** UDP sockets.
- **Client → resolver:** UDP port `5300` by default.
- **Resolver → authoritative server:** UDP port `5301` by default.
- **Server responses:** Send to the source address and source port of the request.
- Both ports must be configurable in a shared configuration file or environment variables.
- On different physical computers, bind the server to the appropriate network interface (or `0.0.0.0` for a controlled lab) and connect using that computer's LAN IP. `127.0.0.1` refers only to the same computer.
- Allow the selected UDP ports through the relevant host firewall on the lab network. Do not expose this educational service to the public internet.

UDP does not guarantee delivery, ordering, or duplicate suppression. The request-ID and retry rules in this document address basic application-level handling.

## 4. Binary Message Format

Every message has an **8-byte fixed header**, followed by the payload.

All multi-byte integer fields use **network byte order (big-endian)**.

### 4.1 Header

| Offset | Size | Field | Meaning |
|---:|---:|---|---|
| 0 | 1 byte | `version` | Protocol version. Version 1 is `0x01`. |
| 1 | 1 byte | `message_type` | Identifies the payload format. |
| 2 | 4 bytes | `request_id` | Unsigned 32-bit ID used to match requests and responses. |
| 6 | 2 bytes | `payload_length` | Unsigned 16-bit payload length in bytes. |
| 8 | Variable | `payload` | Payload defined by `message_type`. |

Python header format: `!BBIH`

- `!` means network byte order.
- `B` is an unsigned 8-bit integer.
- `I` is an unsigned 32-bit integer.
- `H` is an unsigned 16-bit integer.

The total datagram length must equal `8 + payload_length`. Reject a datagram if it is shorter than the header, shorter than the declared payload, or contains unexpected trailing bytes.

**Project safety limit:** Maximum complete UDP datagram size is **1200 bytes**. Reject larger messages. This is a conservative project limit, not a maximum imposed by UDP itself.

### 4.2 Message Types

| Value | Name | Direction | Payload |
|---:|---|---|---|
| `0x01` | `QUERY` | Client → resolver; resolver → authoritative | Domain-name bytes |
| `0x02` | `RESPONSE` | Resolver → client; authoritative → resolver | Status, TTL, address family, IPv4 bytes |
| `0x03` | `ERROR` | Any server → requester | Error code and detail code |
| `0x04` | `HEALTH_CHECK` | Optional request | Empty |
| `0x05` | `HEALTH_RESPONSE` | Optional response | Empty |

Version 1 must implement `QUERY`, `RESPONSE`, and `ERROR`. Health messages are optional and should only be added if the team agrees.

### 4.3 Example Datagrams

Query for `example.com` with request ID `1` (11-byte payload, 19 bytes total):

```
01 01 00 00 00 01 00 0B 65 78 61 6D 70 6C 65 2E 63 6F 6D
|  |  |-----------| |---| |-----------------------------|
|  |  request_id   len=11  "example.com" (ASCII)
|  message_type = QUERY
version = 1
```

Successful response for request ID `1`: `93.184.216.34`, TTL 300 (10-byte payload, 18 bytes total):

```
01 02 00 00 00 01 00 0A 00 00 00 01 2C 04 5D B8 D8 22
|  |  |-----------| |---| |  |-----------| |  |-----------|
|  |  request_id   len=10 |  ttl = 300    |  93.184.216.34
|  message_type = RESPONSE |               address_family = IPv4
version = 1                status = OK
```

## 5. Query Message (`0x01`)

The payload is the domain name encoded as UTF-8 bytes. Version 1 accepts **ASCII domain names only**; internationalized domain-name conversion is outside the initial scope.

### 5.1 Domain Normalization and Validation

Normalize a domain before sending or using it as a cache key:

1. Remove surrounding whitespace from the user-entered string.
2. Convert ASCII letters to lowercase.
3. Remove one trailing dot, if present.
4. Validate the resulting name.

Rules:

- Total normalized name length: 1–253 characters.
- Each label between dots: 1–63 characters.
- Labels contain only ASCII letters, digits, and hyphens.
- A label cannot begin or end with a hyphen.
- No spaces, empty labels, embedded NUL bytes, or non-ASCII characters.
- A trailing dot may be accepted from user input but is removed during normalization.
- IP literals are not accepted as domain queries in version 1.

Examples:

| Input | Result |
|---|---|
| `Example.COM` | Normalize to `example.com` |
| `example.com.` | Normalize to `example.com` |
| `sub.example.com` | Valid |
| `-bad.example` | Reject |
| `bad..example` | Reject |
| `example .com` | Reject |
| Empty input | Reject |

Invalid client input should be reported locally where possible. If a malformed packet reaches a server, the server may return `ERROR/BAD_REQUEST` if it can safely identify the request ID.

## 6. Response Message (`0x02`)

Version 1 response payload is **exactly 10 bytes**, with Python format `!BIB4s`.

| Field | Size | Format | Meaning |
|---|---:|---|---|
| `status` | 1 byte | `B` | `0x00` means success. Other values are reserved; use `ERROR` for failures in version 1. |
| `ttl` | 4 bytes | `I` | TTL in whole seconds, unsigned 32-bit. |
| `address_family` | 1 byte | `B` | `0x04` means IPv4. |
| `ipv4_address` | 4 bytes | `4s` | Four packed IPv4 address bytes. |

Python format: `!BIB4s`

Use Python's `ipaddress.IPv4Address(ip_string).packed` to convert a dotted IPv4 address to four bytes. Convert it back with `str(ipaddress.IPv4Address(packed_bytes))`.

Rules:

- A successful response has `status = 0x00` and `address_family = 0x04`.
- The resolver must validate the payload length, status, family, TTL, and IPv4 address before returning or caching it.
- TTL is the authoritative record's lifetime in seconds. The resolver must not extend it.
- On a cache hit, return the remaining TTL rounded down to a whole second.
- If remaining TTL is zero or less, the entry is expired and must not be served.
- Do not cache errors or malformed responses.

The authoritative server should send the configured record's TTL. The resolver tracks expiry using a monotonic clock, such as Python's `time.monotonic()`, rather than wall-clock time.

## 7. Error Message (`0x03`)

The error payload is **exactly 2 bytes**, encoded using Python format `!BB`.

| Field | Size | Meaning |
|---|---:|---|
| `error_code` | 1 byte | General error category |
| `detail_code` | 1 byte | Optional additional detail; use `0x00` when unused |

### 7.1 Error Codes

| Value | Name | Meaning |
|---:|---|---|
| `0x01` | `BAD_REQUEST` | Invalid domain or malformed request |
| `0x02` | `NOT_FOUND` | Domain is not present in the authoritative record set |
| `0x03` | `SERVFAIL` | Resolver could not complete resolution, for example after upstream timeouts |
| `0x04` | `UNSUPPORTED_VERSION` | Protocol version is not supported |
| `0x05` | `UNSUPPORTED_TYPE` | Message type is not supported |
| `0x06` | `TOO_LARGE` | Message exceeds the configured datagram limit |
| `0x07` | `INTERNAL_ERROR` | Unexpected server-side failure |

Rules:

- Use message type `ERROR` and the same `request_id` as the request when it can be parsed safely.
- If the request ID cannot be parsed because the header is incomplete, a server may use ID `0`.
- Do not include stack traces, credentials, file paths, or internal secrets in network error payloads.
- Version 1 does not cache negative answers (`NOT_FOUND`).

## 8. Optional Health-Check Messages

If implemented, `HEALTH_CHECK` (`0x04`) and `HEALTH_RESPONSE` (`0x05`) both have an empty payload (`payload_length = 0`). The response copies the request ID.

These messages only indicate that the process can respond; they do not prove that every upstream dependency is healthy.

## 9. Request IDs and Response Matching

- Every new logical request gets a request ID from `0` through `2^32 - 1`.
- Do not reuse an ID while a request with that ID is still pending on the same socket.
- A response must match the expected request ID, expected source IP/port, and expected message type.
- The resolver must match upstream responses against the authoritative server's configured IP/port and the outstanding upstream request ID.
- Ignore late responses, duplicate responses, responses from unexpected senders, and responses with mismatched IDs. Log them where useful, but do not treat them as answers.
- A retry of the same logical request uses the same request ID.
- The resolver creates its own upstream request ID; it should not assume the client ID is unique across every client.

## 10. Resolution Workflow

> **Review note:** the source text ended mid-sentence at §10.1 step 7. Everything from that point on (and §§11–15) was completed to match the rest of the specification. Please review it with the team before treating it as agreed.

### 10.1 Cache Miss

1. Client accepts a normal domain string, normalizes/validates it, encodes a `QUERY`, assigns a request ID, and sends it to the resolver.
2. Resolver decodes and validates the complete packet and domain.
3. Resolver checks its cache. If there is no valid entry, it forwards a query to the configured authoritative server.
4. Authoritative server validates the query and looks up the normalized name in its configured record set.
5. If found, authoritative server sends a `RESPONSE` containing the configured IPv4 address and TTL. If not found, it sends `ERROR/NOT_FOUND`.
6. Resolver checks the source address/port, request ID, message type, payload length, status, address family, TTL, and IPv4 data.
7. If valid, resolver applies the agreed cache policy. The policy (LRU baseline or AI-assisted) decides only whether the entry is admitted and which entry is evicted when the cache is full; it never changes the IP address or TTL.
8. Resolver sends a `RESPONSE` to the client's source address and port, using the **client's** request ID and the TTL received from the authoritative server.
9. Client verifies the request ID and sender, decodes the payload, and displays the IP address (and TTL).

### 10.2 Cache Hit

1. Steps 1–2 of the cache-miss flow apply.
2. Resolver finds an entry for the normalized domain and computes the remaining TTL from its monotonic expiry time.
3. If remaining TTL is greater than zero, the resolver sends a `RESPONSE` with the cached IP and the remaining TTL (rounded down). No upstream query is made.
4. If remaining TTL is zero or less, the entry is expired: remove it and handle the request as a cache miss. An expired entry must never be served.

### 10.3 Domain Not Found

If the authoritative server returns `ERROR/NOT_FOUND`, the resolver validates the message (sender, request ID, type, payload) and forwards `ERROR/NOT_FOUND` to the client using the client's request ID. Nothing is cached.

### 10.4 Invalid Request

If the resolver or authoritative server receives a malformed datagram, it may reply with the matching `ERROR` code (`BAD_REQUEST`, `UNSUPPORTED_VERSION`, `UNSUPPORTED_TYPE`, or `TOO_LARGE`) if the request ID can be parsed; otherwise it silently drops the datagram or uses ID `0`. The resolver must not forward an invalid query upstream.

### 10.5 Timeouts and Retries

- The resolver waits up to the **upstream timeout** (default 1.0 s) for each attempt and retries up to **2** times (3 attempts total), reusing the same upstream request ID.
- If every attempt times out, or the upstream reply is malformed, the resolver sends `ERROR/SERVFAIL` to the client. Failures are not cached.
- The client waits up to the **client timeout** (default 4.0 s) per attempt and retries up to **1** time, reusing the same request ID. If all attempts time out it reports a local timeout error.
- The client timeout must be longer than the resolver's worst case (`upstream timeout × attempts`, 3.0 s by default) so the client does not give up before the resolver can answer. If you change one value, check the other.

## 11. AI Cache Policy Interface

The AI policy sits behind a small interface so the resolver can switch between the LRU baseline and the AI policy without changing the protocol.

| Resolver event | Information passed to the policy |
|---|---|
| Query received | Normalized domain, monotonic timestamp |
| Cache hit / miss | Domain, hit or miss, remaining TTL if hit |
| Authoritative answer accepted | Domain, TTL, current cache size and capacity |
| Eviction needed | Candidate domains currently in the cache |

The policy returns only a **recommendation**: admit or reject a new entry, and which candidate to evict. Rules:

- The policy never receives or returns IP addresses it created; cached IP data always comes from a validated authoritative `RESPONSE`.
- The policy must not extend a TTL, resurrect an expired entry, or bypass validation.
- If the policy raises an error or is unavailable, the resolver falls back to LRU and still answers the client.

## 12. Default Configuration

All values must be configurable (shared config file or environment variables). `protocol.py` reads the environment variables below.

| Setting | Default | Environment variable |
|---|---|---|
| Resolver host | `127.0.0.1` | `MINIDNS_RESOLVER_HOST` |
| Resolver port | `5300` | `MINIDNS_RESOLVER_PORT` |
| Authoritative host | `127.0.0.1` | `MINIDNS_AUTH_HOST` |
| Authoritative port | `5301` | `MINIDNS_AUTH_PORT` |
| Client timeout (per attempt) | `4.0` s | `MINIDNS_CLIENT_TIMEOUT` |
| Client retries | `1` | `MINIDNS_CLIENT_RETRIES` |
| Upstream timeout (per attempt) | `1.0` s | `MINIDNS_UPSTREAM_TIMEOUT` |
| Upstream retries | `2` | `MINIDNS_UPSTREAM_RETRIES` |
| Cache capacity | `100` entries | `MINIDNS_CACHE_CAPACITY` |
| Max datagram size | `1200` bytes | (constant in `protocol.py`) |

## 13. Logging for Evaluation

Each component should log one line per event with consistent fields so results can be compared across the LRU and AI policies:

`timestamp, component, request_id, domain, event, latency_ms, ttl, cache_policy`

Suggested `event` values: `QUERY_RECEIVED`, `CACHE_HIT`, `CACHE_MISS`, `CACHE_EXPIRED`, `UPSTREAM_SENT`, `UPSTREAM_TIMEOUT`, `RESPONSE_SENT`, `ERROR_SENT`, `IGNORED_PACKET`.

Do not log secrets or full stack traces to network responses; local log files may contain more detail.

## 14. Compatibility Checklist

Before integration, every member should confirm:

- [ ] Header is `!BBIH`, version `1`, total datagram = `8 + payload_length`, max 1200 bytes.
- [ ] Domains are normalized with the shared `normalize_domain()` before sending and before use as a cache key.
- [ ] `RESPONSE` payload is exactly 10 bytes (`!BIB4s`); `ERROR` payload is exactly 2 bytes (`!BB`).
- [ ] Responses are sent to the request's source address and port.
- [ ] Replies are accepted only from the expected sender with the expected request ID and type.
- [ ] Retries reuse the same request ID; the resolver uses its own upstream IDs.
- [ ] TTL uses `time.monotonic()`; remaining TTL is rounded down; expired entries are never served.
- [ ] Errors and malformed responses are never cached; negative answers are not cached.
- [ ] The AI policy never creates or modifies an IP address or TTL.

## 15. Changing This Specification

Any change to the header, message types, payload layouts, error codes, normalization rules, or default configuration requires agreement from all members. Increment the version (and `PROTOCOL_VERSION` in `protocol.py`) for any change that breaks wire compatibility, and update this document and `protocol.py` together.
