"""Write resolver events using the shared CSV fields and experiment metadata."""

import csv
from pathlib import Path
import time


class QueryLog:
    fields = (
        "timestamp", "component", "request_id", "domain", "event", "latency_ms",
        "ttl", "cache_policy", "upstream_request_id", "udp_payload_bytes", "status",
    )

    def __init__(self, path: Path):
        self.path = path

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open("a", newline="", encoding="utf-8", buffering=1)
        self.writer = csv.writer(self.file)
        if self.file.tell() == 0:
            self.writer.writerow(self.fields)
        return self

    def __exit__(self, *exc):
        self.file.close()

    def write(self, event, request_id, domain, cache_policy, *, latency_ms=None,
              ttl=None, upstream_request_id=None, udp_payload_bytes=None, status=None):
        self.writer.writerow((
            time.time(), "resolver", request_id, domain, event,
            "" if latency_ms is None else f"{latency_ms:.6f}", ttl, cache_policy,
            upstream_request_id, udp_payload_bytes, status,
        ))
