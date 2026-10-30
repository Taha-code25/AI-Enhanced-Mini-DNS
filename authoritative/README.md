# Authoritative DNS Server

## Responsibilities and architecture

The authoritative server is the source of configured DNS-like records for
the project. It accepts a query from the resolver, looks up the normalized
domain in `data/records.json`, and returns the configured IPv4 address and
TTL or a protocol error. It does not recurse, query public DNS, or cache
answers.

The request path is:

```text
Client -> Resolver -> Authoritative server -> Resolver -> Client
```

## UDP request-response workflow

1. The server receives a UDP datagram and validates its version, message
   type, and framing.
2. It accepts only `QUERY`, decodes and validates the domain, then looks
   up the normalized name in its loaded records.
3. It replies to the request's source IP and UDP port, preserving the
   request ID when available.

Known records receive a `RESPONSE`. Unknown valid names receive
`ERROR/NOT_FOUND`. Malformed requests receive the corresponding protocol
error when possible; a safely unavailable request ID is represented as
zero. The server logs unexpected local errors without returning internal
details in an error payload.

## Shared protocol integration

The server uses the shared helpers in `protocol/protocol.py`; it does not
implement a separate wire format. Messages use the version-1 binary UDP
protocol: an `!BBIH` header, QUERY domain payloads, `!BIB4s` successful
response payloads, and `!BB` error payloads. The shared decoder enforces
the 1200-byte datagram limit and reports malformed framing and unsupported
versions.

Supported server outcomes include:

- `RESPONSE`: configured IPv4 address, configured TTL, and original
  request ID.
- `ERROR/NOT_FOUND`: valid domain absent from the record set.
- `ERROR/BAD_REQUEST`: malformed query or invalid domain.
- `ERROR/UNSUPPORTED_VERSION`, `ERROR/UNSUPPORTED_TYPE`, and
  `ERROR/TOO_LARGE`: corresponding protocol validation failures.
- `ERROR/INTERNAL_ERROR`: unexpected request-processing failure.

Optional health-check message types are not handled as health checks by
this server; non-QUERY message types are rejected.

## Record data and TTL

The default record file is resolved relative to the repository, not the
current working directory. It contains a JSON list of objects with
`domain`, `ip`, and `ttl` fields:

```json
[
  {
    "domain": "portal.university.test",
    "ip": "10.0.0.10",
    "ttl": 300
  }
]
```

Domains are normalized and validated when loaded. IP values must be
valid IPv4 addresses, and TTL values must be integers in the protocol's
unsigned 32-bit range. The sample records use TTL 300 seconds. A TTL of
zero is valid in the protocol but will not be cached by the resolver.

## Run locally

From the repository root:

```bash
python -m authoritative.server
```

By default, the server binds to `127.0.0.1:5301`, using
`MINIDNS_AUTH_HOST` and `MINIDNS_AUTH_PORT` from the shared configuration.
The host and port can also be selected with `--bind-host` and `--port`.
For example:

```bash
python -m authoritative.server --bind-host 0.0.0.0 --port 5301
```

Use an appropriate interface and firewall configuration for a controlled
lab deployment; loopback is suitable only when components run on the same
host. An alternate record file can be selected with `--records PATH`.

## Tests and verification

Run the standard-library unit tests and compile check from the repository
root:

```bash
python -m unittest discover -s tests -p "test_authoritative.py" -v
python -m compileall authoritative tests
```

Verified results:

- **10 authoritative unit tests passed.**
- **6 live UDP tests passed** against the localhost authoritative endpoint:
  three sample records, unknown domain, malformed query, and a valid query
  after the malformed request.
- **Localhost end-to-end resolution succeeded:** a client UDP request
  passed through the resolver handler to the authoritative server and
  resolved `portal.university.test` to `10.0.0.10` with TTL 300.

These are localhost results, not validation across three separate LAN
hosts. Wireshark packet analysis and five-run performance measurements
remain pending.
