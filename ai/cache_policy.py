"""TTL-aware cache that evicts the domain least likely to be requested again."""

import pickle
import time

from ai.history import QueryHistory
from protocol.protocol import remaining_ttl
from resolver.cache import DNSCache


class PredictiveCache(DNSCache):
    def __init__(self, capacity, model, history_window):
        super().__init__(capacity)
        if capacity == 0:
            raise ValueError("AI cache capacity must be positive")
        self.model = model
        self.history = QueryHistory(history_window)

    @classmethod
    def from_file(cls, capacity, path):
        with path.open("rb") as stream:
            artifact = pickle.load(stream)
        return cls(capacity, artifact["model"], artifact["history_window"])

    @property
    def policy(self):
        return "ai"

    def get(self, domain):
        self.history.observe(domain, time.monotonic())
        return super().get(domain)

    def _evict(self):
        now = time.monotonic()
        for domain, entry in self.entries.items():
            if remaining_ttl(entry.expiry, now) <= 0:
                del self.entries[domain]
                return
        domains = list(self.entries)
        probabilities = self.model.predict_proba(
            [self.history.features(domain, now) for domain in domains]
        )
        repeat_class = list(self.model.classes_).index(1)
        # OrderedDict keeps the least recently used candidate first on ties.
        victim = min(zip(domains, probabilities), key=lambda pair: pair[1][repeat_class])
        del self.entries[victim[0]]
