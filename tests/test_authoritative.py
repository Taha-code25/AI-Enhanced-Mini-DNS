import json
import socket
import struct
import tempfile
import threading
import unittest
from pathlib import Path

from authoritative.server import (
    AuthoritativeServer,
    RecordConfigurationError,
    load_records,
)
from protocol.protocol import (
    ErrorCode,
    MessageType,
    decode_error_payload,
    decode_message,
    decode_response_payload,
    encode_message,
    encode_query,
)


VALID_RECORDS = [
    {"domain": "portal.university.test", "ip": "10.0.0.10", "ttl": 300},
    {"domain": "library.university.test", "ip": "10.0.0.20", "ttl": 300},
    {"domain": "mail.university.test", "ip": "10.0.0.30", "ttl": 300},
]


class AuthoritativeServerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.records_path = Path(self.temp_dir.name) / "records.json"
        self.write_records(VALID_RECORDS)

        self.server = AuthoritativeServer("127.0.0.1", 0, self.records_path)
        self.server_thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.server_thread.start()
        self.client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.client.bind(("127.0.0.1", 0))
        self.client.settimeout(1)

    def tearDown(self):
        self.client.close()
        self.server.shutdown()
        self.server_thread.join(timeout=2)
        self.temp_dir.cleanup()
        self.assertFalse(self.server_thread.is_alive(), "server thread did not stop")

    def write_records(self, records):
        self.records_path.write_text(json.dumps(records), encoding="utf-8")

    def exchange(self, packet):
        self.client.sendto(packet, self.server.address)
        return self.client.recvfrom(1201)

    def receive_error(self, packet):
        data, _ = self.exchange(packet)
        message = decode_message(data)
        self.assertEqual(message.message_type, MessageType.ERROR)
        return message.request_id, decode_error_payload(message.payload).error_code

    def test_known_domain_returns_address_ttl_and_request_id(self):
        data, sender = self.exchange(
            encode_query(0x12345678, "portal.university.test")
        )
        message = decode_message(data)

        self.assertEqual(sender, self.server.address)
        self.assertEqual(message.message_type, MessageType.RESPONSE)
        self.assertEqual(message.request_id, 0x12345678)
        answer = decode_response_payload(message.payload)
        self.assertEqual((answer.ip, answer.ttl), ("10.0.0.10", 300))

    def test_unknown_domain_returns_not_found(self):
        request_id, error = self.receive_error(encode_query(11, "missing.test"))
        self.assertEqual(request_id, 11)
        self.assertEqual(error, ErrorCode.NOT_FOUND)

    def test_invalid_domain_returns_bad_request(self):
        packet = encode_message(MessageType.QUERY, 12, b"-invalid.test")
        request_id, error = self.receive_error(packet)
        self.assertEqual(request_id, 12)
        self.assertEqual(error, ErrorCode.BAD_REQUEST)

    def test_unsupported_version_returns_unsupported_version(self):
        packet = encode_message(MessageType.QUERY, 13, b"a.test", version=2)
        request_id, error = self.receive_error(packet)
        self.assertEqual(request_id, 13)
        self.assertEqual(error, ErrorCode.UNSUPPORTED_VERSION)

    def test_unsupported_type_returns_unsupported_type(self):
        for packet in (
            encode_message(0x63, 14),
            encode_message(MessageType.RESPONSE, 14),
        ):
            request_id, error = self.receive_error(packet)
            self.assertEqual(request_id, 14)
            self.assertEqual(error, ErrorCode.UNSUPPORTED_TYPE)

    def test_malformed_payload_length_is_rejected(self):
        packet = bytearray(encode_query(15, "a.test"))
        struct.pack_into("!H", packet, 6, len(packet))
        request_id, error = self.receive_error(bytes(packet))
        self.assertEqual(request_id, 15)
        self.assertEqual(error, ErrorCode.BAD_REQUEST)

    def test_oversized_datagram_is_rejected(self):
        packet = encode_query(16, "a.test") + b"x" * 1200
        request_id, error = self.receive_error(packet)
        self.assertEqual(request_id, 16)
        self.assertEqual(error, ErrorCode.TOO_LARGE)

    def test_server_continues_after_malformed_request(self):
        malformed = bytearray(encode_query(17, "a.test"))
        struct.pack_into("!H", malformed, 6, len(malformed))
        self.assertEqual(
            self.receive_error(bytes(malformed))[1], ErrorCode.BAD_REQUEST
        )

        data, _ = self.exchange(encode_query(18, "library.university.test"))
        message = decode_message(data)
        self.assertEqual(message.request_id, 18)
        self.assertEqual(message.message_type, MessageType.RESPONSE)

    def test_response_is_sent_to_requesting_socket(self):
        data, sender = self.exchange(encode_query(19, "mail.university.test"))
        self.assertEqual(sender, self.server.address)
        self.assertEqual(decode_message(data).request_id, 19)

    def test_invalid_record_configuration_is_rejected(self):
        invalid_records = (
            [{"domain": "not a domain", "ip": "10.0.0.1", "ttl": 300}],
            [{"domain": "valid.test", "ip": "2001:db8::1", "ttl": 300}],
            [{"domain": "valid.test", "ip": "10.0.0.1", "ttl": -1}],
            [{"domain": "valid.test", "ip": "10.0.0.1", "ttl": True}],
        )
        for records in invalid_records:
            with self.subTest(records=records):
                self.write_records(records)
                with self.assertRaises(RecordConfigurationError):
                    load_records(self.records_path)


if __name__ == "__main__":
    unittest.main()
