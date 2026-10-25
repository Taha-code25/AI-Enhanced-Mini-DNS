
"""
cache.py - TTL-based DNS cache with LRU eviction.
"""

from collections import OrderedDict
from dataclasses import dataclass
from typing import Optional, Tuple

from protocol.protocol import compute_expiry, remaining_ttl


@dataclass
class CacheEntry:
    ip: str
    expiry: float


class DNSCache:
    """Stores successful DNS lookups using TTL and LRU."""

    def __init__(self, capacity: int = 100):
        if capacity < 0:
            raise ValueError("Cache capacity cannot be negative")

        self.capacity = capacity
        self.entries = OrderedDict()

    def get(self, domain: str) -> Optional[Tuple[str, int]]:
        """
        Return (IP address, remaining TTL) if cached and not expired.
        Return None if missing or expired.
        """
        entry = self.entries.get(domain)

        if entry is None:
            return None

        ttl_left = remaining_ttl(entry.expiry)

        if ttl_left <= 0:
            del self.entries[domain]
            return None

        # Move recently used entries to the end.
        self.entries.move_to_end(domain)

        return entry.ip, ttl_left

    def put(self, domain: str, ip: str, ttl: int) -> None:
        """Save a successful lookup, unless its TTL is zero."""
        if self.capacity == 0 or ttl <= 0:
            return

        # Refresh an existing entry.
        self.entries.pop(domain, None)

        self.entries[domain] = CacheEntry(
            ip=ip,
            expiry=compute_expiry(ttl),
        )

        # Remove least recently used entries if over capacity.
        while len(self.entries) > self.capacity:
            self.entries.popitem(last=False)

    def clear(self) -> None:
        """Remove every cached entry."""
        self.entries.clear()

    def __len__(self) -> int:
        """Return the number of entries currently stored."""
        return len(self.entries)
