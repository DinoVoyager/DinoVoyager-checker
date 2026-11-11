"""Unit tests for checker.dns module."""

import struct
from unittest.mock import MagicMock, patch

from checker.dns import (
    _build_dns_wire_query,
    _parse_dns_wire_response,
    check_dns_health,
    probe_udp,
    probe_tcp,
    probe_doh,
    probe_dot,
)


def test_build_dns_wire_query():
    qid, packet = _build_dns_wire_query("example.com")
    assert isinstance(qid, int)
    assert 1 <= qid <= 65535
    assert len(packet) > 12
    # Verify header
    unpacked_qid, flags, qd, an, ns, ar = struct.unpack("!HHHHHH", packet[:12])
    assert unpacked_qid == qid
    assert flags == 0x0100
    assert qd == 1


def test_parse_dns_wire_response_valid():
    qid = 1234
    header = struct.pack("!HHHHHH", qid, 0x8180, 1, 1, 0, 0)
    qname = b"\x07example\x03com\x00"
    question = qname + struct.pack("!HH", 1, 1)
    # Answer record: pointer to name (0xC00C), A record (type 1), class 1, ttl 300, rdlen 4, IP 93.184.216.34
    answer = b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, 300, 4) + bytes([93, 184, 216, 34])
    data = header + question + answer

    parsed = _parse_dns_wire_response(data, expected_qid=qid)
    assert parsed["status"] == "ok"
    assert parsed["rcode"] == 0
    assert parsed["answers"] == 1
    assert "93.184.216.34" in parsed["ips"]


def test_parse_dns_wire_response_short_or_mismatch():
    assert _parse_dns_wire_response(b"short", 123)["status"] == "error"
    header = struct.pack("!HHHHHH", 9999, 0x8180, 1, 0, 0, 0)
    assert _parse_dns_wire_response(header, 1111)["error"] == "id_mismatch"


def test_check_dns_health_mocked():
    with patch("checker.dns.probe_udp", return_value={"status": "ok", "latency_ms": 10.0, "suspected_spoofing": False}), \
         patch("checker.dns.probe_tcp", return_value={"status": "ok", "latency_ms": 15.0, "suspected_spoofing": False}), \
         patch("checker.dns.probe_doh", return_value={"status": "ok", "latency_ms": 25.0, "suspected_spoofing": False}), \
         patch("checker.dns.probe_dot", return_value={"status": "ok", "latency_ms": 20.0, "suspected_spoofing": False}):
        
        result = check_dns_health(timeout=1.0)
        assert result["spoofing_detected"] is False
        assert len(result["observations"]) == 16
        for obs in result["observations"]:
            assert obs["status"] == "ok"
            assert obs["outcome"]["type"] == "Answer"
