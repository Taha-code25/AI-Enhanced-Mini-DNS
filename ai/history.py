"""Shared query-history features for training and cache eviction."""

from collections import defaultdict, deque


class QueryHistory:
    def __init__(self, window_seconds):
        self.window_seconds = window_seconds
        self.events = deque()
        self.by_domain = defaultdict(deque)

    def observe(self, domain, now):
        cutoff = now - self.window_seconds
        while self.events and self.events[0][0] < cutoff:
            _, expired_domain = self.events.popleft()
            timestamps = self.by_domain[expired_domain]
            timestamps.popleft()
            if not timestamps:
                del self.by_domain[expired_domain]
        self.events.append((now, domain))
        self.by_domain[domain].append(now)

    def features(self, domain, now):
        timestamps = self.by_domain.get(domain)
        if not timestamps:
            return (0, self.window_seconds)
        return (len(timestamps), max(0.0, now - timestamps[-1]))
