"""Unit tests for checker.reporter module (13 test cases)."""

import json
import socket
from unittest.mock import MagicMock, patch
import urllib.error
import urllib.request

import pytest

from checker.config import Config
from checker.geo import GeoInfo
from checker.reporter import assemble_payload, send_report
from checker.tester import RouteResult, TestSummary


def _make_response(data, status: int = 200):
    body = json.dumps(data).encode("utf-8") if isinstance(data, (dict, list)) else data
    resp = MagicMock()
    resp.read.return_value = body
    resp.status = status
    resp.code = status
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = None
    return resp


@pytest.fixture
def sample_config():
    return Config(
        subscription_url="https://app.dinovoyager.net/api/sub/token",
        polling_interval=60,
        api_url="https://app.dinovoyager.net",
        api_key="dvc_live_testkey1234567890abcdef",
        checker_name="Home-Frankfurt-Telekom",
    )


@pytest.fixture
def sample_geo():
    return GeoInfo(
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


@pytest.fixture
def sample_routes():
    return [
        RouteResult("EE-VLESS", "64.188.82.195", 443, "vless", "EE", True, 38.0),
        RouteResult("NL-VLESS", "2.27.40.72", 443, "vless", "NL", True, 19.0),
        RouteResult("FI-VLESS", "191.217.252.52", 443, "vless", "FI", False, None),
    ]


@pytest.fixture
def sample_summary():
    return TestSummary(
        total_routes=3,
        online_routes=2,
        avg_latency_ms=28.5,
        min_latency_ms=19.0,
        max_latency_ms=38.0,
        reliability_pct=66.7,
    )


def test_reporter_assemble_payload_schema_conformance(
    sample_config, sample_geo, sample_routes, sample_summary
):
    """1. Validate assembled payload against Contract #1."""
    payload = assemble_payload(sample_config, sample_geo, sample_routes, sample_summary)

    assert payload["api_key"] == sample_config.api_key
    assert payload["checker_version"] == "1.0.0"
    assert isinstance(payload["timestamp"], int)
    assert isinstance(payload["collected_at"], str)
    assert payload["collected_at"].endswith("Z")

    info = payload["checker_info"]
    assert info["name"] == "Home-Frankfurt-Telekom"
    assert info["ip"] == "84.116.142.50"
    assert info["isp"] == "Deutsche Telekom AG"
    assert info["org"] == "Deutsche Telekom"
    assert info["asn"] == "AS3320"
    assert info["country"] == "Germany"
    assert info["country_code"] == "DE"
    assert info["city"] == "Frankfurt am Main"
    assert info["latitude"] == 50.1109
    assert info["longitude"] == 8.6821

    assert len(payload["routes"]) == 3
    r0 = payload["routes"][0]
    assert r0["name"] == "EE-VLESS"
    assert r0["server"] == "64.188.82.195"
    assert r0["port"] == 443
    assert r0["protocol"] == "vless"
    assert r0["location_code"] == "EE"
    assert r0["online"] is True
    assert r0["latency_ms"] == 38.0

    sum_data = payload["summary"]
    assert sum_data["total_routes"] == 3
    assert sum_data["online_routes"] == 2
    assert sum_data["avg_latency_ms"] == 28.5
    assert sum_data["reliability_pct"] == 66.7


def test_reporter_send_report_success_http_200(
    sample_config, sample_geo, sample_routes, sample_summary
):
    """2. Successful report POST returns True."""
    payload = assemble_payload(sample_config, sample_geo, sample_routes, sample_summary)

    mock_resp = _make_response({"ok": True, "received_at": 1741500000})
    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        success = send_report(payload, sample_config)

    assert success is True
    assert mock_urlopen.call_count == 1
    req = mock_urlopen.call_args[0][0]
    assert req.full_url == "https://app.dinovoyager.net/api/checkers/report"
    assert req.get_method() == "POST"


def test_reporter_headers_verification(
    sample_config, sample_geo, sample_routes, sample_summary
):
    """3. Verify HTTP headers sent with request."""
    payload = assemble_payload(sample_config, sample_geo, sample_routes, sample_summary)

    captured_requests = []

    def _mock_urlopen(req, *args, **kwargs):
        captured_requests.append(req)
        return _make_response({"ok": True})

    with patch("urllib.request.urlopen", side_effect=_mock_urlopen):
        send_report(payload, sample_config)

    assert len(captured_requests) == 1
    req = captured_requests[0]
    headers = {k.lower(): v for k, v in req.headers.items()}
    assert headers.get("content-type") == "application/json"
    assert headers.get("x-api-key") == sample_config.api_key
    assert "DinoVoyager-Checker" in headers.get("user-agent", "")


def test_reporter_custom_checker_name_preservation(
    sample_config, sample_geo, sample_routes, sample_summary
):
    """4. User-defined checker name is preserved."""
    sample_config.checker_name = "My-Home-Lab"
    payload = assemble_payload(sample_config, sample_geo, sample_routes, sample_summary)
    assert payload["checker_info"]["name"] == "My-Home-Lab"


def test_reporter_auto_generated_checker_name(
    sample_config, sample_geo, sample_routes, sample_summary
):
    """5. Auto-generated checker name combines hostname and ISP."""
    sample_config.checker_name = None
    sample_geo.isp = "Vodafone"

    with patch("socket.gethostname", return_value="nas"):
        payload = assemble_payload(sample_config, sample_geo, sample_routes, sample_summary)

    name = payload["checker_info"]["name"]
    assert "nas" in name or "Vodafone" in name


def test_reporter_unauthorized_http_401(
    sample_config, sample_geo, sample_routes, sample_summary
):
    """6. HTTP 401 returns False immediately without rapid retry."""
    payload = assemble_payload(sample_config, sample_geo, sample_routes, sample_summary)

    mock_urlopen = MagicMock(
        side_effect=urllib.error.HTTPError(
            "https://app.dinovoyager.net/api/checkers/report", 401, "Unauthorized", {}, None
        )
    )

    with patch("urllib.request.urlopen", mock_urlopen):
        success = send_report(payload, sample_config)

    assert success is False
    assert mock_urlopen.call_count == 1


def test_reporter_forbidden_http_403(
    sample_config, sample_geo, sample_routes, sample_summary
):
    """7. HTTP 403 returns False immediately without rapid retry."""
    payload = assemble_payload(sample_config, sample_geo, sample_routes, sample_summary)

    mock_urlopen = MagicMock(
        side_effect=urllib.error.HTTPError(
            "https://app.dinovoyager.net/api/checkers/report", 403, "Forbidden", {}, None
        )
    )

    with patch("urllib.request.urlopen", mock_urlopen):
        success = send_report(payload, sample_config)

    assert success is False
    assert mock_urlopen.call_count == 1


def test_reporter_retry_on_http_502_success_after_retry(
    sample_config, sample_geo, sample_routes, sample_summary
):
    """8. HTTP 502 recovery on retry."""
    payload = assemble_payload(sample_config, sample_geo, sample_routes, sample_summary)

    mock_urlopen = MagicMock(
        side_effect=[
            urllib.error.HTTPError("url", 502, "Bad Gateway", {}, None),
            _make_response({"ok": True}),
        ]
    )

    with patch("urllib.request.urlopen", mock_urlopen), patch("time.sleep"):
        success = send_report(payload, sample_config, max_retries=3, backoff_delays=[0.01, 0.02])

    assert success is True
    assert mock_urlopen.call_count == 2


def test_reporter_retry_on_http_503_service_unavailable(
    sample_config, sample_geo, sample_routes, sample_summary
):
    """9. HTTP 503 recovery on retry."""
    payload = assemble_payload(sample_config, sample_geo, sample_routes, sample_summary)

    mock_urlopen = MagicMock(
        side_effect=[
            urllib.error.HTTPError("url", 503, "Service Unavailable", {}, None),
            _make_response({"ok": True}),
        ]
    )

    with patch("urllib.request.urlopen", mock_urlopen), patch("time.sleep"):
        success = send_report(payload, sample_config, max_retries=3, backoff_delays=[0.01, 0.02])

    assert success is True
    assert mock_urlopen.call_count == 2


def test_reporter_retry_exhaustion_on_persistent_500(
    sample_config, sample_geo, sample_routes, sample_summary
):
    """10. Server continuously down (3 retries failed) returns False without crashing."""
    payload = assemble_payload(sample_config, sample_geo, sample_routes, sample_summary)

    mock_urlopen = MagicMock(
        side_effect=urllib.error.HTTPError("url", 500, "Internal Server Error", {}, None)
    )

    with patch("urllib.request.urlopen", mock_urlopen), patch("time.sleep"):
        success = send_report(payload, sample_config, max_retries=3, backoff_delays=[0.01, 0.02])

    assert success is False
    assert mock_urlopen.call_count == 3


def test_reporter_fallback_endpoint_on_404(
    sample_config, sample_geo, sample_routes, sample_summary
):
    """11. Fallback from /api/checkers/report to /checkers/report on 404."""
    payload = assemble_payload(sample_config, sample_geo, sample_routes, sample_summary)

    def _mock_urlopen(req, *args, **kwargs):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if "/api/checkers/report" in url:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        if "/checkers/report" in url:
            return _make_response({"ok": True})
        raise urllib.error.URLError("Unexpected URL")

    with patch("urllib.request.urlopen", side_effect=_mock_urlopen):
        success = send_report(payload, sample_config)

    assert success is True


def test_reporter_connection_timeout_retry(
    sample_config, sample_geo, sample_routes, sample_summary
):
    """12. Transient network timeout retry succeeds."""
    payload = assemble_payload(sample_config, sample_geo, sample_routes, sample_summary)

    mock_urlopen = MagicMock(
        side_effect=[
            socket.timeout("Connection timed out"),
            _make_response({"ok": True}),
        ]
    )

    with patch("urllib.request.urlopen", mock_urlopen), patch("time.sleep"):
        success = send_report(payload, sample_config, max_retries=2, backoff_delays=[0.01])

    assert success is True
    assert mock_urlopen.call_count == 2


def test_reporter_json_serialization_data_types(
    sample_config, sample_geo, sample_routes, sample_summary
):
    """13. Ensure all nested data types serialize cleanly to JSON."""
    payload = assemble_payload(sample_config, sample_geo, sample_routes, sample_summary)
    dumped = json.dumps(payload)
    assert isinstance(dumped, str)
    reloaded = json.loads(dumped)
    assert reloaded["summary"]["avg_latency_ms"] == 28.5
    assert reloaded["routes"][0]["online"] is True
