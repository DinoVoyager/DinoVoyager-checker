"""Adversarial stress-testing suite for checker.geo and checker.reporter.

Designed and executed by Empirical Challenger to stress-test failure modes,
edge cases, boundary conditions, and contract strictness.
"""

from __future__ import annotations

import json
import logging
import socket
from typing import Any, Dict
from unittest.mock import MagicMock, patch
import urllib.error
import urllib.request

import pytest

from checker.config import Config
from checker.geo import (
    GEO_CACHE_TTL,
    GeoDetector,
    GeoInfo,
    clear_cache,
    detect_geo,
    normalize_asn,
)
from checker.reporter import (
    CHECKER_VERSION,
    assemble_payload,
    mask_api_key,
    send_report,
)
from checker.tester import RouteResult, TestSummary


def _make_response(data: Any, status: int = 200) -> MagicMock:
    """Helper to mock an HTTP response context manager."""
    body = json.dumps(data).encode("utf-8") if isinstance(data, (dict, list)) else data
    resp = MagicMock()
    resp.read.return_value = body
    resp.status = status
    resp.code = status
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = None
    return resp


# ============================================================================
# PART 1: GEO RESOLUTION STRESS TESTS
# ============================================================================


def test_geo_http_429_rate_limiting_cascade():
    """Stress 1: Cascade when primary and secondary return HTTP 429 rate limit."""
    clear_cache()

    def _mock_urlopen(req, *args, **kwargs):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if "ip-api.com" in url:
            raise urllib.error.HTTPError(url, 429, "Too Many Requests", {}, None)
        if "ipinfo.io" in url:
            raise urllib.error.HTTPError(url, 429, "Rate limit exceeded", {}, None)
        if "ipwhois.app" in url:
            return _make_response(
                {
                    "success": True,
                    "ip": "194.67.210.15",
                    "isp": "Selectel",
                    "org": "Selectel LLC",
                    "asn": "49505",
                    "country": "Russia",
                    "country_code": "RU",
                    "city": "Saint Petersburg",
                    "latitude": 59.9343,
                    "longitude": 30.3351,
                }
            )
        raise urllib.error.URLError("Unexpected URL")

    with patch("urllib.request.urlopen", side_effect=_mock_urlopen):
        geo = detect_geo()

    assert geo.ip == "194.67.210.15"
    assert geo.isp == "Selectel"
    assert geo.asn == "AS49505"
    assert geo.country_code == "RU"
    assert geo.latitude == 59.9343
    assert geo.longitude == 30.3351


def test_geo_partial_truncated_json_primary():
    """Stress 2: Primary provider returns truncated JSON chunk."""
    clear_cache()

    def _mock_urlopen(req, *args, **kwargs):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if "ip-api.com" in url:
            # Severed connection in middle of stream
            return _make_response(b'{"status":"success","country":"Germa')
        if "ipinfo.io" in url:
            return _make_response(
                {
                    "ip": "84.116.142.50",
                    "country": "DE",
                    "city": "Frankfurt",
                    "loc": "50.1109,8.6821",
                    "org": "AS3320 Deutsche Telekom AG",
                }
            )
        raise urllib.error.URLError("Unexpected URL")

    with patch("urllib.request.urlopen", side_effect=_mock_urlopen):
        geo = detect_geo()

    assert geo.country_code == "DE"
    assert geo.city == "Frankfurt"
    assert geo.latitude == 50.1109
    assert geo.longitude == 8.6821


def test_geo_invalid_utf8_bytes_stream():
    """Stress 3: Primary provider returns malformed non-UTF8 byte sequence."""
    clear_cache()

    def _mock_urlopen(req, *args, **kwargs):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if "ip-api.com" in url:
            return _make_response(b"\xff\xfe\xfd\x80\x81\x00")
        if "ipinfo.io" in url:
            return _make_response(
                {
                    "ip": "84.116.142.50",
                    "country": "DE",
                    "city": "Frankfurt",
                    "loc": "50.1109,8.6821",
                    "org": "AS3320 Deutsche Telekom AG",
                }
            )
        raise urllib.error.URLError("Unexpected URL")

    with patch("urllib.request.urlopen", side_effect=_mock_urlopen):
        geo = detect_geo()

    assert geo.country_code == "DE"
    assert geo.city == "Frankfurt"


def test_geo_socket_timeout_primary_and_secondary():
    """Stress 4: Primary and secondary suffer socket timeouts."""
    clear_cache()

    def _mock_urlopen(req, *args, **kwargs):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if "ip-api.com" in url:
            raise socket.timeout("Socket connection timed out")
        if "ipinfo.io" in url:
            raise TimeoutError("Read timeout")
        if "ipwhois.app" in url:
            return _make_response(
                {
                    "success": True,
                    "ip": "84.116.142.50",
                    "isp": "DTAG",
                    "country": "Germany",
                    "country_code": "DE",
                    "city": "Frankfurt",
                    "latitude": 50.11,
                    "longitude": 8.68,
                }
            )
        raise urllib.error.URLError("Unexpected URL")

    with patch("urllib.request.urlopen", side_effect=_mock_urlopen):
        geo = detect_geo()

    assert geo.country_code == "DE"
    assert geo.ip == "84.116.142.50"


def test_geo_dns_resolution_failure_cascade():
    """Stress 5: DNS failure on primary and secondary triggers tertiary."""
    clear_cache()

    def _mock_urlopen(req, *args, **kwargs):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if "ip-api.com" in url:
            raise urllib.error.URLError(socket.gaierror(-3, "Temporary failure in name resolution"))
        if "ipinfo.io" in url:
            raise urllib.error.URLError(socket.gaierror(-2, "Name or service not known"))
        if "ipwhois.app" in url:
            return _make_response(
                {
                    "success": True,
                    "ip": "1.1.1.1",
                    "isp": "Cloudflare",
                    "country": "Australia",
                    "country_code": "AU",
                    "city": "Sydney",
                    "latitude": -33.86,
                    "longitude": 151.20,
                }
            )
        raise urllib.error.URLError("Unexpected URL")

    with patch("urllib.request.urlopen", side_effect=_mock_urlopen):
        geo = detect_geo()

    assert geo.country_code == "AU"
    assert geo.latitude == -33.86
    assert geo.longitude == 151.20


def test_geo_complete_internet_blackout_graceful():
    """Stress 6: Total network blackout returns fallback without crashing."""
    clear_cache()

    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Network unreachable")):
        geo = detect_geo(checker_name="AdversarialNode")

    assert geo.ip == "0.0.0.0"
    assert geo.isp == "Unknown"
    assert geo.asn == "Unknown"
    assert geo.country == "Unknown"
    assert geo.country_code == "UNKNOWN"
    assert geo.city == "Unknown"
    assert geo.latitude == 0.0
    assert geo.longitude == 0.0
    assert geo.name == "AdversarialNode"


def test_geo_cache_ttl_strict_boundary():
    """Stress 7: Strict boundary testing at TTL - 1s (hit) vs TTL + 1s (miss)."""
    clear_cache()
    mock_resp = _make_response(
        {
            "status": "success",
            "query": "1.1.1.1",
            "country": "Germany",
            "countryCode": "DE",
            "city": "Berlin",
            "lat": 52.52,
            "lon": 13.40,
            "isp": "ISP",
            "as": "AS100",
        }
    )

    t0 = 1000000.0
    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen, patch(
        "time.time", side_effect=lambda: t0
    ):
        # 1. Fetch initial
        g1 = detect_geo()
        assert mock_urlopen.call_count == 1
        assert g1.ip == "1.1.1.1"

        # 2. Advance to 14,399s (1s before 4h TTL) -> must be CACHE HIT
        t0 += 14399.0
        g2 = detect_geo()
        assert mock_urlopen.call_count == 1
        assert g2.ip == "1.1.1.1"

        # 3. Advance by 2s to 14,401s (1s after 4h TTL) -> must be CACHE MISS
        t0 += 2.0
        g3 = detect_geo()
        assert mock_urlopen.call_count == 2
        assert g3.ip == "1.1.1.1"


def test_geo_dynamic_ip_flapping_across_cycles():
    """Stress 8: Repeated IP flapping across cycles invalidates cache immediately."""
    clear_cache()

    def _make_ip_resp(ip_addr: str) -> MagicMock:
        return _make_response(
            {
                "status": "success",
                "query": ip_addr,
                "country": "Country",
                "countryCode": "CC",
                "city": "City",
                "lat": 10.0,
                "lon": 20.0,
                "isp": "ISP",
                "as": "AS100",
            }
        )

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = [
            _make_ip_resp("100.1.1.1"),
            _make_ip_resp("200.2.2.2"),
            _make_ip_resp("100.1.1.1"),
            _make_ip_resp("300.3.3.3"),
        ]

        # Cycle 1: 100.1.1.1
        g1 = detect_geo(current_ip="100.1.1.1")
        assert g1.ip == "100.1.1.1"
        assert mock_urlopen.call_count == 1

        # Cycle 2: Same IP -> cache hit
        g2 = detect_geo(current_ip="100.1.1.1")
        assert g2.ip == "100.1.1.1"
        assert mock_urlopen.call_count == 1

        # Cycle 3: Flap to 200.2.2.2 -> invalidated, refetched
        g3 = detect_geo(current_ip="200.2.2.2")
        assert g3.ip == "200.2.2.2"
        assert mock_urlopen.call_count == 2

        # Cycle 4: Flap back to 100.1.1.1 -> invalidated, refetched
        g4 = detect_geo(current_ip="100.1.1.1")
        assert g4.ip == "100.1.1.1"
        assert mock_urlopen.call_count == 3

        # Cycle 5: Flap to 300.3.3.3 -> invalidated, refetched
        g5 = detect_geo(current_ip="300.3.3.3")
        assert g5.ip == "300.3.3.3"
        assert mock_urlopen.call_count == 4


def test_geo_fallback_cached_during_startup_blackout():
    """Stress 9: Empirical discovery: Caching fallback GeoInfo during startup blackout.

    If the initial probe fails due to network blackout at boot, fallback GeoInfo (0.0.0.0)
    is cached for 4 hours, which suppresses recovery queries even when the network is restored!
    """
    clear_cache()

    # Step 1: Initial startup probe fails
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Network down")):
        g_blackout = detect_geo()
        assert g_blackout.ip == "0.0.0.0"

    # Step 2: 10 seconds later, network is restored
    mock_success = _make_response(
        {
            "status": "success",
            "query": "84.116.142.50",
            "country": "Germany",
            "countryCode": "DE",
            "city": "Frankfurt",
            "lat": 50.11,
            "lon": 8.68,
            "isp": "DTAG",
            "as": "AS3320",
        }
    )

    with patch("urllib.request.urlopen", return_value=mock_success) as mock_urlopen:
        # Caller calls detect_geo() without parameters (as in __main__.py)
        g_restored = detect_geo()

        # EMPIRICAL OBSERVATION:
        # mock_urlopen is NOT called because fallback was cached with 4h TTL!
        # g_restored is still 0.0.0.0 Unknown!
        assert mock_urlopen.call_count == 0
        assert g_restored.ip == "0.0.0.0"


# ============================================================================
# PART 2: REPORTER STRESS TESTS
# ============================================================================


def test_reporter_schema_strictness_all_required_contract_fields():
    """Stress 10: Strict schema verification per PROJECT.md § Interface Contracts: 1."""
    cfg = Config(
        subscription_url="https://app.dinovoyager.net/api/sub/token",
        polling_interval=60,
        api_url="https://app.dinovoyager.net",
        api_key="dvc_live_1234567890abcdef",
        checker_name="Test-Checker",
    )
    geo = GeoInfo(
        ip="84.116.142.50",
        isp="Deutsche Telekom AG",
        org="Deutsche Telekom",
        asn="AS3320",
        country="Germany",
        country_code="DE",
        city="Frankfurt am Main",
        latitude=50.1109,
        longitude=8.6821,
    )
    routes = [
        RouteResult("EE-VLESS", "64.188.82.195", 443, "vless", "EE", True, 38.0),
        RouteResult("FI-VLESS", "191.217.252.52", 443, "vless", "FI", False, None),
    ]
    summary = TestSummary(
        total_routes=2,
        online_routes=1,
        avg_latency_ms=38.0,
        min_latency_ms=38.0,
        max_latency_ms=38.0,
        reliability_pct=50.0,
    )

    payload = assemble_payload(cfg, geo, routes, summary)

    # 1. Top-level contract keys
    expected_top_keys = {
        "api_key",
        "checker_version",
        "timestamp",
        "collected_at",
        "checker_info",
        "routes",
        "summary",
    }
    assert expected_top_keys.issubset(payload.keys())

    assert isinstance(payload["api_key"], str) and payload["api_key"] == cfg.api_key
    assert isinstance(payload["checker_version"], str) and payload["checker_version"] == CHECKER_VERSION
    assert isinstance(payload["timestamp"], int)
    assert isinstance(payload["collected_at"], str) and payload["collected_at"].endswith("Z")

    # 2. checker_info contract keys (10 fields)
    info = payload["checker_info"]
    expected_info_keys = {
        "name",
        "ip",
        "isp",
        "org",
        "asn",
        "country",
        "country_code",
        "city",
        "latitude",
        "longitude",
    }
    assert expected_info_keys.issubset(info.keys())
    assert isinstance(info["name"], str)
    assert isinstance(info["ip"], str)
    assert isinstance(info["isp"], str)
    assert isinstance(info["org"], str)
    assert isinstance(info["asn"], str)
    assert isinstance(info["country"], str)
    assert isinstance(info["country_code"], str)
    assert isinstance(info["city"], str)
    assert isinstance(info["latitude"], float)
    assert isinstance(info["longitude"], float)

    # 3. routes contract keys (7 fields per route)
    assert isinstance(payload["routes"], list) and len(payload["routes"]) == 2
    expected_route_keys = {
        "name",
        "server",
        "port",
        "protocol",
        "location_code",
        "online",
        "latency_ms",
    }
    for r in payload["routes"]:
        assert expected_route_keys.issubset(r.keys())
        assert isinstance(r["name"], str)
        assert isinstance(r["server"], str)
        assert isinstance(r["port"], int)
        assert isinstance(r["protocol"], str)
        assert isinstance(r["location_code"], str)
        assert isinstance(r["online"], bool)
        assert isinstance(r["latency_ms"], float)

    # 4. summary contract keys (4 fields)
    s = payload["summary"]
    expected_summary_keys = {
        "total_routes",
        "online_routes",
        "avg_latency_ms",
        "reliability_pct",
    }
    assert expected_summary_keys.issubset(s.keys())
    assert isinstance(s["total_routes"], int)
    assert isinstance(s["online_routes"], int)
    assert isinstance(s["avg_latency_ms"], float)
    assert isinstance(s["reliability_pct"], float)

    # 5. Must dump cleanly to JSON
    raw_json = json.dumps(payload)
    reloaded = json.loads(raw_json)
    assert reloaded["summary"]["online_routes"] == 1


def test_reporter_schema_edge_cases_empty_routes():
    """Stress 11: Empty routes subscription produces clean schema."""
    cfg = Config("http://sub", 60, "http://api", "dvc_live_key")
    geo = GeoInfo(ip="0.0.0.0")
    payload = assemble_payload(cfg, geo, routes=[], summary={})

    assert payload["routes"] == []
    assert payload["summary"]["total_routes"] == 0
    assert payload["summary"]["online_routes"] == 0
    assert payload["summary"]["avg_latency_ms"] == 0.0
    assert payload["summary"]["reliability_pct"] == 0.0


def test_reporter_payload_null_fields_vulnerabilities():
    """Stress 12: Empirical discovery: assemble_payload crashes when fields are None in dict.

    If geo contains {"latitude": None, "longitude": None}, float(None) raises TypeError.
    If summary contains {"avg_latency_ms": None}, float(None) raises TypeError.
    If routes is None, iterating raises TypeError.
    """
    cfg = Config("http://sub", 60, "http://api", "dvc_live_key")

    # Null latitude in geo dict
    with pytest.raises(TypeError, match="float.*argument must be a string or a real number, not 'NoneType'"):
        assemble_payload(cfg, {"latitude": None}, [], {})

    # Null avg_latency_ms in summary dict
    with pytest.raises(TypeError, match="float.*argument must be a string or a real number, not 'NoneType'"):
        assemble_payload(cfg, {}, [], {"avg_latency_ms": None})

    # None routes
    with pytest.raises(TypeError, match="'NoneType' object is not iterable"):
        assemble_payload(cfg, {}, None, {})


def test_reporter_500_502_503_exponential_backoff():
    """Stress 13: 502 -> 503 -> 200 sequence verifies exponential retry delays."""
    cfg = Config("http://sub", 60, "http://api.test", "dvc_live_key")
    payload = {"api_key": "dvc_live_key"}

    mock_resp_502 = urllib.error.HTTPError("http://api.test/api/checkers/report", 502, "Bad Gateway", {}, None)
    mock_resp_503 = urllib.error.HTTPError("http://api.test/api/checkers/report", 503, "Service Unavailable", {}, None)
    mock_resp_200 = _make_response({"ok": True})

    delays = []
    with patch(
        "urllib.request.urlopen", side_effect=[mock_resp_502, mock_resp_503, mock_resp_200]
    ) as mock_urlopen, patch("time.sleep", side_effect=lambda d: delays.append(d)):
        success = send_report(payload, cfg, max_retries=3, backoff_delays=[1.0, 2.0, 4.0])

    assert success is True
    assert mock_urlopen.call_count == 3
    assert delays == [1.0, 2.0]


def test_reporter_persistent_500_exhaustion():
    """Stress 14: Persistent 500 error returns False after exactly max_retries."""
    cfg = Config("http://sub", 60, "http://api.test", "dvc_live_key")
    payload = {"api_key": "dvc_live_key"}

    mock_resp_500 = urllib.error.HTTPError("http://api.test/api/checkers/report", 500, "Internal Server Error", {}, None)

    delays = []
    with patch("urllib.request.urlopen", side_effect=mock_resp_500) as mock_urlopen, patch(
        "time.sleep", side_effect=lambda d: delays.append(d)
    ):
        success = send_report(payload, cfg, max_retries=3, backoff_delays=[1.0, 2.0])

    assert success is False
    assert mock_urlopen.call_count == 3
    assert delays == [1.0, 2.0]


def test_reporter_401_unauthorized_no_retry_storm_and_masked_key():
    """Stress 15: 401 returns False immediately, no retries, key masked in log."""
    raw_key = "dvc_live_SECRETKEY998877665544332211"
    cfg = Config("http://sub", 60, "http://api.test", raw_key)
    payload = {"api_key": raw_key}

    # Setup log capture
    captured_logs = []

    class CapturingHandler(logging.Handler):
        def emit(self, record):
            captured_logs.append(self.format(record))

    logger = logging.getLogger("dinovoyager-checker.reporter")
    handler = CapturingHandler()
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)

    try:
        mock_401 = urllib.error.HTTPError("http://api.test/api/checkers/report", 401, "Unauthorized", {}, None)
        with patch("urllib.request.urlopen", side_effect=mock_401) as mock_urlopen, patch(
            "time.sleep"
        ) as mock_sleep:
            success = send_report(payload, cfg, max_retries=3)

        assert success is False
        # ZERO RETRIES
        assert mock_urlopen.call_count == 1
        assert mock_sleep.call_count == 0

        # LOG AUDIT: Never leak raw API key
        all_logs = " ".join(captured_logs)
        assert raw_key not in all_logs, "CRITICAL: Raw API key leaked into reporter log!"
        assert mask_api_key(raw_key) in all_logs, "Masked API key not present in rejection log!"
    finally:
        logger.removeHandler(handler)


def test_reporter_403_forbidden_no_retry_storm_and_masked_key():
    """Stress 16: 403 returns False immediately, no retries, key masked in log."""
    raw_key = "dvc_live_SECRETKEY998877665544332211"
    cfg = Config("http://sub", 60, "http://api.test", raw_key)
    payload = {"api_key": raw_key}

    captured_logs = []

    class CapturingHandler(logging.Handler):
        def emit(self, record):
            captured_logs.append(self.format(record))

    logger = logging.getLogger("dinovoyager-checker.reporter")
    handler = CapturingHandler()
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)

    try:
        mock_403 = urllib.error.HTTPError("http://api.test/api/checkers/report", 403, "Forbidden", {}, None)
        with patch("urllib.request.urlopen", side_effect=mock_403) as mock_urlopen, patch(
            "time.sleep"
        ) as mock_sleep:
            success = send_report(payload, cfg, max_retries=3)

        assert success is False
        assert mock_urlopen.call_count == 1
        assert mock_sleep.call_count == 0

        all_logs = " ".join(captured_logs)
        assert raw_key not in all_logs, "CRITICAL: Raw API key leaked into reporter log!"
        assert mask_api_key(raw_key) in all_logs
    finally:
        logger.removeHandler(handler)


def test_reporter_socket_reset_peer_recovery():
    """Stress 17: Socket reset (ConnectionResetError) triggers backoff and recovery."""
    cfg = Config("http://sub", 60, "http://api.test", "dvc_live_key")
    payload = {"api_key": "dvc_live_key"}

    mock_reset = ConnectionResetError(10054, "Connection reset by peer")
    mock_resp_200 = _make_response({"ok": True})

    delays = []
    with patch(
        "urllib.request.urlopen", side_effect=[mock_reset, mock_resp_200]
    ) as mock_urlopen, patch("time.sleep", side_effect=lambda d: delays.append(d)):
        success = send_report(payload, cfg, max_retries=3, backoff_delays=[2.0, 5.0])

    assert success is True
    assert mock_urlopen.call_count == 2
    assert delays == [2.0]


def test_reporter_socket_reset_exhaustion():
    """Stress 18: Repeated socket resets exhaust retries gracefully."""
    cfg = Config("http://sub", 60, "http://api.test", "dvc_live_key")
    payload = {"api_key": "dvc_live_key"}

    mock_reset = ConnectionResetError(10054, "Connection reset by peer")

    delays = []
    with patch("urllib.request.urlopen", side_effect=mock_reset) as mock_urlopen, patch(
        "time.sleep", side_effect=lambda d: delays.append(d)
    ):
        success = send_report(payload, cfg, max_retries=3, backoff_delays=[1.0, 2.0])

    assert success is False
    assert mock_urlopen.call_count == 3
    assert delays == [1.0, 2.0]


def test_reporter_fallback_url_404_recovery():
    """Stress 19: Primary URL gives 404, fallback delivers report successfully."""
    cfg = Config("http://sub", 60, "http://api.test", "dvc_live_key")
    payload = {"api_key": "dvc_live_key"}

    def _mock_urlopen(req, *args, **kwargs):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if "/api/checkers/report" in url:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        if "/checkers/report" in url:
            return _make_response({"ok": True, "received_at": 1741500000})
        raise urllib.error.URLError("Unexpected URL")

    with patch("urllib.request.urlopen", side_effect=_mock_urlopen):
        success = send_report(payload, cfg)

    assert success is True
