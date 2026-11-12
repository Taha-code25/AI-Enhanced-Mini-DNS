"""Member 4 regression checks that run without opening network sockets."""

import csv
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from ai.cache_policy import PredictiveCache
from ai.history import QueryHistory
from authoritative.server import handle_datagram, load_records
from logging_system.query_log import QueryLog
from protocol.protocol import Config, RequestIdGenerator, encode_query
from resolver.cache import DNSCache
from resolver.resolver import handle_query


class QueryLoggingTests(unittest.TestCase):
    def test_csv_correlates_miss_hit_and_error_with_client_queries(self):
        config = Config()
        records = load_records()
        cache = DNSCache()
        request_ids = RequestIdGenerator()
        client = Mock()
        upstream = Mock()

        def answer(packet, server):
            reply = Mock()
            handle_datagram(packet, ("127.0.0.1", 49152), reply, records)
            upstream.recvfrom.return_value = (reply.sendto.call_args.args[0], server)

        upstream.sendto.side_effect = answer
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "queries.csv"
            with QueryLog(path) as log, patch(
                "resolver.resolver.socket.socket"
            ) as open_socket:
                open_socket.return_value.__enter__.return_value = upstream
                for request_id, domain in enumerate((
                    "portal.university.test", "portal.university.test", "missing.test",
                ), start=1):
                    handle_query(
                        encode_query(request_id, domain), ("127.0.0.1", 50000),
                        client, config, cache, request_ids, log,
                    )
            with path.open(newline="", encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))

        events = {
            request_id: [row["event"] for row in rows if row["request_id"] == request_id]
            for request_id in ("1", "2", "3")
        }
        self.assertEqual(events["1"], [
            "QUERY_RECEIVED", "CACHE_MISS", "UPSTREAM_SENT", "UPSTREAM_RECEIVED",
            "RESPONSE_SENT",
        ])
        self.assertEqual(events["2"], ["QUERY_RECEIVED", "CACHE_HIT", "RESPONSE_SENT"])
        self.assertEqual(events["3"], [
            "QUERY_RECEIVED", "CACHE_MISS", "UPSTREAM_SENT", "UPSTREAM_RECEIVED",
            "ERROR_SENT",
        ])
        self.assertEqual(upstream.sendto.call_count, 2)
        self.assertEqual(rows[-1]["status"], "NOT_FOUND")
        self.assertGreaterEqual(float(rows[-1]["latency_ms"]), 0)
        self.assertTrue(all(row["cache_policy"] == "lru" for row in rows))


class PredictiveCacheTests(unittest.TestCase):
    def test_eviction_uses_prediction_and_ttl_still_applies(self):
        model = Mock(classes_=[0, 1])
        model.predict_proba.side_effect = lambda rows: [
            (0.1, 0.9) if count > 1 else (0.9, 0.1) for count, _ in rows
        ]
        cache = PredictiveCache(2, model, 60)
        with patch("time.monotonic", return_value=100) as clock:
            self.assertIsNone(cache.get("frequent.test"))
            cache.put("frequent.test", "10.0.0.1", 10)
            self.assertEqual(cache.get("frequent.test"), ("10.0.0.1", 10))
            self.assertIsNone(cache.get("rare.test"))
            cache.put("rare.test", "10.0.0.2", 10)
            self.assertIsNone(cache.get("new.test"))
            cache.put("new.test", "10.0.0.3", 10)
            self.assertIsNone(cache.get("rare.test"))
            self.assertEqual(cache.get("frequent.test"), ("10.0.0.1", 10))
            cache.put("zero.test", "10.0.0.4", 0)
            self.assertIsNone(cache.get("zero.test"))
            clock.return_value = 110
            self.assertIsNone(cache.get("frequent.test"))

    def test_history_discards_old_observations(self):
        history = QueryHistory(5)
        history.observe("old.test", 1)
        history.observe("old.test", 2)
        history.observe("new.test", 7)
        self.assertEqual(history.features("old.test", 7), (1, 5))
        history.observe("new.test", 8)
        self.assertEqual(history.features("old.test", 8), (0, 5))
