"""DNS Tampering and Spoofing inspection module for DinoVoyager Checker.

Implements zero-dependency probes modeled after Cheburcheck:
- Tests standard resolvers: Cloudflare (1.1.1.1), Google (8.8.8.8), Quad9 (9.9.9.9), Yandex (77.88.8.8).
- Tests 4 transports: UDP (53), TCP (53), DoH (443), and DoT (853).
- Detects NXDOMAIN hijacking, blackholing/poisoning, and UDP/TCP 53 transport blocking.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
import random
import socket
import ssl
import struct
import time
from typing import Any, Dict, List, Optional, Tuple
import urllib.request

logger = logging.getLogger("dinovoyager-checker.dns")

# Standard public resolvers to probe
RESOLVERS: Dict[str, Dict[str, Any]] = {
    "cloudflare": {
        "name": "Cloudflare",
        "ip": "1.1.1.1",
        "doh": "https://cloudflare-dns.com/dns-query",
        "dot_host": "cloudflare-dns.com",
    },
    "google": {
        "name": "Google",
        "ip": "8.8.8.8",
        "doh": "https://dns.google/dns-query",
        "dot_host": "dns.google",
    },
    "quad9": {
        "name": "Quad9",
        "ip": "9.9.9.9",
        "doh": "https://dns.quad9.net/dns-query",
        "dot_host": "dns.quad9.net",
    },
    "yandex": {
        "name": "Yandex",
        "ip": "77.88.8.8",
        "doh": "https://dns.yandex.ru/dns-query",
        "dot_host": "common.dot.dns.yandex.net",
    },
}

POISON_BLACKHOLE_IPS = {"0.0.0.0", "127.0.0.1"}
CANARY_DOMAIN = "example.com"


def _build_dns_wire_query(domain: str, qtype: int = 1) -> Tuple[int, bytes]:
    """Build standard DNS wire format query (Header + Question)."""
    qid = random.randint(1, 65535)
    # Flags: 0x0100 (RD = 1, standard query)
    header = struct.pack("!HHHHHH", qid, 0x0100, 1, 0, 0, 0)
    parts = domain.strip(".").split(".")
    qname = b"".join(bytes([len(p)]) + p.encode("ascii") for p in parts if p) + b"\x00"
    question = qname + struct.pack("!HH", qtype, 1)  # QTYPE, QCLASS=IN
    return qid, header + question


def _parse_dns_wire_response(data: bytes, expected_qid: int) -> Dict[str, Any]:
    """Parse DNS wire format response and extract RCODE, answers count, and IP addresses."""
    if len(data) < 12:
        return {"status": "error", "error": "short_packet", "answers": 0, "ips": []}

    res_id, flags, qdcount, ancount, _, _ = struct.unpack("!HHHHHH", data[:12])
    if res_id != expected_qid:
        return {"status": "error", "error": "id_mismatch", "answers": 0, "ips": []}

    qr = (flags >> 15) & 1
    if qr == 0:
        return {"status": "error", "error": "not_a_response", "answers": 0, "ips": []}

    rcode = flags & 0x000F

    # Parse answer IPs if any
    ips: List[str] = []
    try:
        offset = 12
        # Skip question section(s)
        for _ in range(qdcount):
            if offset >= len(data):
                break
            while offset < len(data):
                b = data[offset]
                if b == 0:
                    offset += 1
                    break
                elif (b & 0xC0) == 0xC0:
                    offset += 2
                    break
                else:
                    offset += 1 + b
            offset += 4  # QTYPE + QCLASS

        # Parse answers
        for _ in range(ancount):
            if offset >= len(data):
                break
            # Skip RR name
            while offset < len(data):
                b = data[offset]
                if b == 0:
                    offset += 1
                    break
                elif (b & 0xC0) == 0xC0:
                    offset += 2
                    break
                else:
                    offset += 1 + b
            if offset + 10 > len(data):
                break
            rtype, rclass, rttl, rdlen = struct.unpack("!HHIH", data[offset : offset + 10])
            offset += 10
            if rtype == 1 and rdlen == 4 and offset + 4 <= len(data):
                ip_str = socket.inet_ntoa(data[offset : offset + 4])
                ips.append(ip_str)
            offset += rdlen
    except Exception as exc:
        logger.debug("Failed to fully parse DNS response records: %s", exc)

    return {"status": "ok", "rcode": rcode, "answers": ancount, "ips": ips}


def probe_udp(ip: str, timeout: float = 2.5) -> Dict[str, Any]:
    """Test UDP DNS on port 53 for both normal resolution and NXDOMAIN hijacking."""
    t0 = time.monotonic()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)

    try:
        # 1. Canary query
        qid, pkt = _build_dns_wire_query(CANARY_DOMAIN)
        sock.sendto(pkt, (ip, 53))
        resp_data, _ = sock.recvfrom(2048)
        lat = round((time.monotonic() - t0) * 1000, 1)

        parsed = _parse_dns_wire_response(resp_data, qid)
        if parsed.get("status") != "ok":
            return {"status": "error", "error": parsed.get("error"), "latency_ms": lat, "suspected_spoofing": False}

        if parsed.get("rcode") != 0 or parsed.get("answers", 0) == 0:
            return {"status": "error", "error": f"rcode_{parsed.get('rcode')}", "latency_ms": lat, "suspected_spoofing": False}

        # Check for blackholed canary IPs
        for ans_ip in parsed.get("ips", []):
            if ans_ip in POISON_BLACKHOLE_IPS:
                return {"status": "spoofed", "error": "poisoned_ip", "latency_ms": lat, "suspected_spoofing": True}

        # 2. NXDOMAIN check (test if resolver hijacks non-existent domain)
        random_sub = f"nxdomain-{random.randint(100000, 999999)}.invalid"
        nx_qid, nx_pkt = _build_dns_wire_query(random_sub)
        sock.sendto(nx_pkt, (ip, 53))
        nx_resp, _ = sock.recvfrom(2048)
        nx_parsed = _parse_dns_wire_response(nx_resp, nx_qid)

        # Standard compliant DNS returns NXDOMAIN (RCODE 3) with 0 answers
        if nx_parsed.get("rcode") == 0 or nx_parsed.get("answers", 0) > 0:
            return {
                "status": "spoofed",
                "error": "nxdomain_hijacking",
                "latency_ms": lat,
                "suspected_spoofing": True,
            }

        return {"status": "ok", "latency_ms": lat, "suspected_spoofing": False}

    except socket.timeout:
        return {"status": "error", "error": "timeout", "latency_ms": None, "suspected_spoofing": False}
    except Exception as exc:
        return {"status": "error", "error": str(exc), "latency_ms": None, "suspected_spoofing": False}
    finally:
        sock.close()


def probe_tcp(ip: str, timeout: float = 3.0) -> Dict[str, Any]:
    """Test TCP DNS on port 53."""
    t0 = time.monotonic()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)

    try:
        sock.connect((ip, 53))
        qid, pkt = _build_dns_wire_query(CANARY_DOMAIN)
        # TCP prefixes DNS packet with 2-byte length
        tcp_pkt = struct.pack("!H", len(pkt)) + pkt
        sock.sendall(tcp_pkt)

        len_bytes = sock.recv(2)
        if len(len_bytes) < 2:
            return {"status": "error", "error": "short_length", "latency_ms": None, "suspected_spoofing": False}
        resp_len = struct.unpack("!H", len_bytes)[0]
        resp_data = sock.recv(resp_len)
        lat = round((time.monotonic() - t0) * 1000, 1)

        parsed = _parse_dns_wire_response(resp_data, qid)
        if parsed.get("status") != "ok" or parsed.get("rcode") != 0 or parsed.get("answers", 0) == 0:
            return {"status": "error", "error": parsed.get("error") or f"rcode_{parsed.get('rcode')}", "latency_ms": lat, "suspected_spoofing": False}

        for ans_ip in parsed.get("ips", []):
            if ans_ip in POISON_BLACKHOLE_IPS:
                return {"status": "spoofed", "error": "poisoned_ip", "latency_ms": lat, "suspected_spoofing": True}

        return {"status": "ok", "latency_ms": lat, "suspected_spoofing": False}

    except socket.timeout:
        return {"status": "error", "error": "timeout", "latency_ms": None, "suspected_spoofing": False}
    except Exception as exc:
        return {"status": "error", "error": str(exc), "latency_ms": None, "suspected_spoofing": False}
    finally:
        sock.close()


def probe_doh(url: str, timeout: float = 3.5) -> Dict[str, Any]:
    """Test DNS-over-HTTPS (DoH)."""
    t0 = time.monotonic()
    try:
        qid, pkt = _build_dns_wire_query(CANARY_DOMAIN)
        req = urllib.request.Request(
            url,
            data=pkt,
            headers={
                "Content-Type": "application/dns-message",
                "Accept": "application/dns-message",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) DinoVoyager-Checker",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp_data = resp.read()
            lat = round((time.monotonic() - t0) * 1000, 1)

        parsed = _parse_dns_wire_response(resp_data, qid)
        if parsed.get("status") != "ok" or parsed.get("rcode") != 0 or parsed.get("answers", 0) == 0:
            return {"status": "error", "error": parsed.get("error") or f"rcode_{parsed.get('rcode')}", "latency_ms": lat, "suspected_spoofing": False}

        return {"status": "ok", "latency_ms": lat, "suspected_spoofing": False}

    except urllib.error.HTTPError as http_err:
        lat = round((time.monotonic() - t0) * 1000, 1)
        # Some providers return HTTP 200 or 400 if method is rejected
        return {"status": "error", "error": f"HTTP_{http_err.code}", "latency_ms": lat, "suspected_spoofing": False}
    except Exception as exc:
        return {"status": "error", "error": str(exc), "latency_ms": None, "suspected_spoofing": False}


def probe_dot(ip: str, dot_host: str, timeout: float = 3.5) -> Dict[str, Any]:
    """Test DNS-over-TLS (DoT) on port 853."""
    t0 = time.monotonic()
    ctx = ssl.create_default_context()
    raw_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    raw_sock.settimeout(timeout)

    try:
        tls_sock = ctx.wrap_socket(raw_sock, server_hostname=dot_host)
        tls_sock.connect((ip, 853))

        qid, pkt = _build_dns_wire_query(CANARY_DOMAIN)
        tcp_pkt = struct.pack("!H", len(pkt)) + pkt
        tls_sock.sendall(tcp_pkt)

        len_bytes = tls_sock.recv(2)
        if len(len_bytes) < 2:
            return {"status": "error", "error": "short_length", "latency_ms": None, "suspected_spoofing": False}
        resp_len = struct.unpack("!H", len_bytes)[0]
        resp_data = tls_sock.recv(resp_len)
        lat = round((time.monotonic() - t0) * 1000, 1)

        parsed = _parse_dns_wire_response(resp_data, qid)
        if parsed.get("status") != "ok" or parsed.get("rcode") != 0 or parsed.get("answers", 0) == 0:
            return {"status": "error", "error": parsed.get("error") or f"rcode_{parsed.get('rcode')}", "latency_ms": lat, "suspected_spoofing": False}

        return {"status": "ok", "latency_ms": lat, "suspected_spoofing": False}

    except socket.timeout:
        return {"status": "error", "error": "timeout", "latency_ms": None, "suspected_spoofing": False}
    except Exception as exc:
        return {"status": "error", "error": str(exc), "latency_ms": None, "suspected_spoofing": False}
    finally:
        try:
            raw_sock.close()
        except Exception:
            pass


def check_dns_health(timeout: float = 3.5) -> Dict[str, Any]:
    """Execute concurrent DNS checks across Cloudflare, Google, Quad9, Yandex resolvers.

    Returns structured observations formatted for DinoVoyager and Cheburcheck backend.
    """
    observations: List[Dict[str, Any]] = []
    tasks = []

    with ThreadPoolExecutor(max_workers=8) as pool:
        for prov_key, cfg in RESOLVERS.items():
            ip = cfg["ip"]
            doh_url = cfg["doh"]
            dot_host = cfg["dot_host"]

            tasks.append((prov_key, "udp", pool.submit(probe_udp, ip, timeout)))
            tasks.append((prov_key, "tcp", pool.submit(probe_tcp, ip, timeout)))
            tasks.append((prov_key, "doh", pool.submit(probe_doh, doh_url, timeout)))
            tasks.append((prov_key, "dot", pool.submit(probe_dot, ip, dot_host, timeout)))

        for prov_key, proto, fut in tasks:
            try:
                res = fut.result()
            except Exception as exc:
                res = {"status": "error", "error": str(exc), "latency_ms": None, "suspected_spoofing": False}

            status = res.get("status", "error")
            suspected = bool(res.get("suspected_spoofing", False))
            is_ok = status == "ok"

            observations.append({
                "provider": prov_key,
                "protocol": proto,
                "status": status,
                "suspected_spoofing": suspected,
                "latency_ms": res.get("latency_ms"),
                "outcome": {"type": "Answer" if is_ok else "Error"},
                "error": res.get("error"),
            })

    spoofing_detected = any(obs.get("suspected_spoofing") for obs in observations)

    return {
        "spoofing_detected": spoofing_detected,
        "observations": observations,
        "timestamp": int(time.time()),
    }
