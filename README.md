# AI-Enhanced Mini DNS

A Computer Communication Networks (CCN) project that implements a small
DNS-like application-layer service using direct socket programming. The
system includes a client, a caching resolver, an AI-assisted cache
policy, and an authoritative server.

The project explores whether query-history-based predictions can improve
cache decisions compared with a conventional Least Recently Used (LRU)
cache.

> **Important:** The AI model predicts which domain records may be
> requested again. It does not generate IP addresses or replace the
> authoritative server. Every cached answer must obey its TTL.

## Project objectives

-   Implement a custom application-layer protocol for DNS-like queries
    and responses.
-   Communicate between independent hosts using sockets.
-   Resolve configured domain names through an authoritative server.
-   Implement caching, TTL expiry, timeout/retry handling, and error
    responses.
-   Record query history and performance telemetry.
-   Compare no-cache, LRU-cache, and AI-assisted cache strategies.
-   Evaluate response time, cache hit ratio, upstream requests,
    throughput, and traffic usage.
-   Capture and inspect network packets with Wireshark.

## Architecture

``` text
+-------------------+
| Client            |
| Sends domain query|
+---------+---------+
          |
          | QUERY
          v
+---------------------------+
| Caching Resolver + AI     |
| - Protocol validation     |
| - Cache and TTL           |
| - Timeout/retry           |
| - Query logging           |
| - AI cache policy         |
+-------------+-------------+
              |
              | Upstream QUERY on cache miss
              v
+---------------------------+
| Authoritative Server      |
| Configured domain records |
+---------------------------+
```

The project uses three running components across three independent
hosts: the client, resolver, and authoritative server. Two team members
collaborate on the resolver and its AI functionality.

## Team modules

  -----------------------------------------------------------------------
  Team member             Module                  Main responsibilities
  ----------------------- ----------------------- -----------------------
  Member 1                `client/`               Send queries, display
                                                  answers, and handle
                                                  errors/timeouts

  Member 2                `resolver/`             Cache lookup, TTL
                                                  validation, upstream
                                                  forwarding, LRU,
                                                  timeout/retry

  Member 3                `ai/` and               Query telemetry,
                          `logging_system/`       feature engineering,
                                                  prediction model, AI
                                                  cache policy,
                                                  evaluation

  Member 4                `authoritative/` and    Store configured
                          `data/`                 records and answer
                                                  valid queries
  -----------------------------------------------------------------------

The `protocol/` directory contains the shared protocol specification and
message-handling code. Agree on protocol fields, ports, encoding, and
status codes before the modules are integrated.

## Example records

These are fictional records for local testing, not public DNS records.

  Domain                      Example IP address
  --------------------------- --------------------
  `portal.university.test`    `10.0.0.10`
  `library.university.test`   `10.0.0.20`
  `mail.university.test`      `10.0.0.30`

## Technology

-   **Language:** Python 3.12 recommended
-   **Networking:** Python `socket` module
-   **Protocol:** Team-defined application-layer protocol (for example,
    UTF-8 JSON over UDP)
-   **AI/ML:** scikit-learn
-   **Data analysis:** pandas and NumPy
-   **Plots:** Matplotlib
-   **Testing:** pytest
-   **Packet analysis:** Wireshark

The Python standard library provides sockets, JSON handling, timestamps,
logging, and other basic functionality; these do not need to be
installed separately.

## Setup

### 1. Clone the repository

``` bash
git clone <YOUR_REPOSITORY_URL>
cd AI-Enhanced-Mini-DNS
```

Replace `<YOUR_REPOSITORY_URL>` with your team's actual Git repository
URL.

### 2. Create and activate a virtual environment

**Windows PowerShell**

``` powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks activation, you can run the environment's Python
directly:

``` powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

**Linux/macOS**

``` bash
python3.12 -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

``` bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### 4. Configure the hosts and ports

Use the agreed protocol document in `protocol/PROTOCOL.md` as the source
of truth. Configure the resolver's listening address and port, and the
authoritative server's address and port, according to your actual
network.

When running on separate computers, bind listening services to the
appropriate network interface (or `0.0.0.0` where suitable) and connect
using the host's reachable LAN IP address. Do not use `127.0.0.1` to
connect between different computers. Check firewall rules and ensure all
devices can communicate.

## Running the components

Run each component from the repository root in its own terminal, on the
assigned host:

1.  Start the authoritative server (Member 4).
2.  Start the resolver (Member 2), configured with the authoritative
    server's reachable IP and port.
3.  Start the client (Member 1), configured with the resolver's
    reachable IP and port.
4.  Run the AI/cache policy integration (Member 3) as imported code or
    through the entry point agreed by the team.

The exact commands depend on the Python entry-point filenames and
functions implemented in this repository. Update this section with the
verified commands once those files are ready; do not assume filenames
before they exist.

## Query flow

1.  The client sends a query containing a request ID and domain name.
2.  The resolver validates the message and checks its cache.
3.  If a valid, unexpired cache entry exists, the resolver returns it
    and records a cache hit.
4.  Otherwise, the resolver queries the authoritative server.
5.  The authoritative server returns the configured record or a defined
    error.
6.  The resolver validates the answer, caches valid records according to
    TTL and cache policy, and responds to the client.
7.  The resolver records the outcome and timing information.

If the authoritative server does not respond within the configured
timeout, the resolver performs only the agreed bounded number of retries
and then returns a documented error. Expired records must not silently
be served as valid.

## AI-assisted caching

The initial baseline should be a correct conventional resolver with TTL
enforcement and LRU caching. The AI module can then use features such
as:

-   Number of queries for a domain in the last five minutes.
-   Number of queries in the last hour.
-   Time since the domain's last query.
-   Hour-of-day patterns, if relevant.
-   Remaining TTL for a cached record.

A possible prediction target is whether a domain will be requested again
within the next five minutes. Train and evaluate using separate time
periods or workloads to reduce data leakage.

Compare these strategies on the same requests:

1.  **No cache:** forward every query to the authoritative server.
2.  **LRU cache:** use conventional caching with TTL enforcement.
3.  **AI-assisted cache:** use predictions to influence cache admission,
    retention, or eviction while keeping TTL enforcement identical.

AI performance must be measured rather than assumed. The AI policy may
not outperform LRU on every workload.

## Testing and evaluation

Test at least these cases:

-   Known domain on a cache miss.
-   Repeated query resulting in a cache hit.
-   TTL expiry followed by a new upstream query.
-   Unknown domain.
-   Malformed query.
-   Authoritative server unavailable.
-   Timeout and bounded retry behavior.
-   Resolver remains responsive after an error.

Measure and report:

-   Average and 95th-percentile response time.
-   Cache hit ratio.
-   Upstream query count.
-   Application-level throughput.
-   Bytes transferred.
-   Failure-to-error time and recovery behavior.

Repeat each measurement at least five times and report averages and
spread. Test at least two workloads, such as repeated requests and
mostly unique domains. Use Wireshark to capture and annotate packets.
Only report results that have actually been measured.

## Suggested development milestones

1.  **Protocol and host setup:** agree on message formats, ports, status
    codes, and the three-host arrangement.
2.  **Basic end-to-end lookup:** client → resolver → authoritative
    server, initially without AI.
3.  **Caching and reliability:** TTL, LRU, timeout/retry, unknown-domain
    and malformed-query handling.
4.  **AI and logging:** collect query history, engineer features, train
    the model, and integrate the cache policy.
5.  **Evaluation and report:** compare all three cache strategies,
    capture packets, plot measured results, and document design
    trade-offs.

## Repository layout

``` text
AI-Enhanced-Mini-DNS/
├── ai/
├── authoritative/
├── client/
├── data/
├── logging_system/
├── protocol/
├── resolver/
├── results/
├── README.md
└── requirements.txt
```

This is the intended high-level layout. Keep existing files and
`__init__.py` modules, and add files only when needed by the
implementation.

## Contributing with Git

Create a separate feature branch for substantial work:

``` bash
git switch -c feature/your-module
```

After making and testing your changes:

``` bash
git status
git add <files-you-changed>
git commit -m "Describe the change"
git push -u origin feature/your-module
```

Use a pull request or your team's agreed merge process to integrate
changes. Coordinate edits to shared protocol and resolver files to avoid
conflicts.

## Limitations and responsible reporting

-   The configured records are experimental and do not replace public
    DNS.
-   The authoritative server remains the source of record truth.
-   The cache must never treat an expired record as valid.
-   Predictions are estimates, not guarantees.
-   Energy savings should be described as estimates unless power is
    measured directly.
-   Follow your course's requirements for acknowledging AI assistance.

## License

See the `LICENSE` file in this repository.
