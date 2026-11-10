"""Unit tests for checker.geo module (20 comprehensive test cases)."""

from __future__ import annotations

import json
import socket
from typing import Any, Dict
from unittest.mock import MagicMock, patch
import urllib.error
import urllib.request

import pytest

from checker.geo import (
    GeoDetector,
    GeoInfo,
    clear_cache,
    detect_geo,
    normalize_asn,
)


def _make_response(data, status: int = 200):
    """Helper to create a mock HTTP response context manager."""
    body = json.dumps(data).encode("utf-8") if isinstance(data, (dict, list)) else data
    resp = MagicMock()
    resp.read.return_value = body
    resp.status = status
    resp.code = status
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = None
    return resp


def test_geo_primary_success(mock_ip_api_response):
    """1. Full healthy query to ip-api.com."""
    with patch("urllib.request.urlopen", return_value=_make_response(mock_ip_api_response)):
        geo = detect_geo()

    assert geo.ip == "84.116.142.50"
    assert geo.isp == "Deutsche Telekom AG"
    assert geo.asn == "AS3320"
    assert geo.country == "Germany"
    assert geo.country_code == "DE"
    assert geo.city == "Frankfurt am Main"
    assert geo.latitude == 50.1109
    assert geo.longitude == 8.6821


def test_geo_primary_failure_fallback_to_ipinfo(mock_ipinfo_response):
    """2. Primary raises HTTP 500; fallback to ipinfo.io succeeds."""
    def _mock_urlopen(req, *args, **kwargs):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if "ip-api.com" in url:
            raise urllib.error.HTTPError(url, 500, "Internal Server Error", {}, None)
        if "ipinfo.io" in url:
            return _make_response(mock_ipinfo_response)
        raise urllib.error.URLError("Not mocked")

    with patch("urllib.request.urlopen", side_effect=_mock_urlopen):
        geo = detect_geo()

    assert geo.country_code == "DE"
    assert geo.city == "Frankfurt am Main"
    assert geo.latitude == 50.1109
    assert geo.longitude == 8.6821
    assert geo.asn == "AS3320"


def test_geo_primary_status_fail_fallback(mock_ipinfo_response):
    """3. Primary returns 200 but status='fail' -> triggers fallback to ipinfo.io."""
    fail_resp = {"status": "fail", "message": "reserved range"}

    def _mock_urlopen(req, *args, **kwargs):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if "ip-api.com" in url:
            return _make_response(fail_resp)
        if "ipinfo.io" in url:
            return _make_response(mock_ipinfo_response)
        raise urllib.error.URLError("Not mocked")

    with patch("urllib.request.urlopen", side_effect=_mock_urlopen):
        geo = detect_geo()

    assert geo.country_code == "DE"
    assert geo.asn == "AS3320"


def test_geo_primary_timeout_fallback(mock_ipinfo_response):
    """4. Primary times out -> triggers fallback to ipinfo.io."""
    def _mock_urlopen(req, *args, **kwargs):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if "ip-api.com" in url:
            raise socket.timeout("Connection timed out")
        if "ipinfo.io" in url:
            return _make_response(mock_ipinfo_response)
        raise urllib.error.URLError("Not mocked")

    with patch("urllib.request.urlopen", side_effect=_mock_urlopen):
        geo = detect_geo()

    assert geo.country_code == "DE"
    assert geo.city == "Frankfurt am Main"


def test_geo_primary_malformed_json_fallback(mock_ipinfo_response):
    """5. Primary returns non-JSON payload -> fallback to ipinfo.io."""
    def _mock_urlopen(req, *args, **kwargs):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if "ip-api.com" in url:
            return _make_response(b"<html>502 Bad Gateway</html>")
        if "ipinfo.io" in url:
            return _make_response(mock_ipinfo_response)
        raise urllib.error.URLError("Not mocked")

    with patch("urllib.request.urlopen", side_effect=_mock_urlopen):
        geo = detect_geo()

    assert geo.country_code == "DE"
    assert geo.asn == "AS3320"


def test_geo_secondary_failure_fallback_to_tertiary(mock_ipwhois_response):
    """6. Primary and secondary fail -> fallback to ipwhois.app."""
    def _mock_urlopen(req, *args, **kwargs):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if "ip-api.com" in url:
            raise urllib.error.HTTPError(url, 500, "Internal Server Error", {}, None)
        if "ipinfo.io" in url:
            raise urllib.error.HTTPError(url, 429, "Too Many Requests", {}, None)
        if "ipwhois.app" in url:
            return _make_response(mock_ipwhois_response)
        raise urllib.error.URLError("Not mocked")

    with patch("urllib.request.urlopen", side_effect=_mock_urlopen):
        geo = detect_geo()

    assert geo.country_code == "DE"
    assert geo.city == "Frankfurt am Main"
    assert geo.asn == "AS3320"
    assert geo.isp == "Deutsche Telekom AG"


def test_geo_all_providers_fail_graceful_defaults():
    """7. All external providers fail -> returns fallback default GeoInfo without crashing."""
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Total network failure")):
        geo = detect_geo()

    assert geo.ip == "0.0.0.0"
    assert geo.isp == "Unknown"
    assert geo.country == "Unknown"
    assert geo.country_code == "UNKNOWN"
    assert geo.latitude == 0.0
    assert geo.longitude == 0.0


def test_geo_caching_within_ttl(mock_ip_api_response):
    """8. Cache hit within 4-hour window makes exactly 1 HTTP call."""
    mock_urlopen = MagicMock(return_value=_make_response(mock_ip_api_response))

    fake_time = 100000.0
    with patch("urllib.request.urlopen", mock_urlopen), patch("time.time", side_effect=lambda: fake_time):
        geo1 = detect_geo()
        assert mock_urlopen.call_count == 1

        # Advance time by 3600 seconds (1 hour, well within 4h TTL)
        fake_time += 3600.0
        geo2 = detect_geo()
        assert mock_urlopen.call_count == 1
        assert geo1.ip == geo2.ip


def test_geo_caching_ttl_expired(mock_ip_api_response):
    """9. Cache expires after 14,400s -> fresh HTTP call executed."""
    mock_urlopen = MagicMock(return_value=_make_response(mock_ip_api_response))

    fake_time = 100000.0
    with patch("urllib.request.urlopen", mock_urlopen), patch("time.time", side_effect=lambda: fake_time):
        detect_geo()
        assert mock_urlopen.call_count == 1

        # Advance time past 14400 seconds
        fake_time += 14401.0
        detect_geo()
        assert mock_urlopen.call_count == 2


def test_geo_caching_ip_change_invalidation(mock_ip_api_response):
    """10. Public IP change detected within TTL invalidates cache and triggers fresh query."""
    new_ip_response = dict(mock_ip_api_response)
    new_ip_response["query"] = "91.200.55.12"

    mock_urlopen = MagicMock(
        side_effect=[
            _make_response(mock_ip_api_response),
            _make_response(new_ip_response),
        ]
    )

    with patch("urllib.request.urlopen", mock_urlopen):
        geo1 = detect_geo()
        assert geo1.ip == "84.116.142.50"
        assert mock_urlopen.call_count == 1

        # Query with changed IP
        geo2 = detect_geo(current_ip="91.200.55.12")
        assert geo2.ip == "91.200.55.12"
        assert mock_urlopen.call_count == 2


def test_geo_force_refresh_bypasses_cache(mock_ip_api_response):
    """11. Parameter force_refresh=True bypasses cache."""
    mock_urlopen = MagicMock(return_value=_make_response(mock_ip_api_response))

    with patch("urllib.request.urlopen", mock_urlopen):
        detect_geo()
        assert mock_urlopen.call_count == 1

        detect_geo(force_refresh=True)
        assert mock_urlopen.call_count == 2


def test_geo_asn_normalization():
    """12. Standardize ASN formats into 'AS<number>'."""
    assert normalize_asn("AS3320 Deutsche Telekom") == "AS3320"
    assert normalize_asn("AS3320") == "AS3320"
    assert normalize_asn("3320") == "AS3320"
    assert normalize_asn("as12345") == "AS12345"
    assert normalize_asn(None) == "Unknown"
    assert normalize_asn("") == "Unknown"


def test_geo_coordinate_parsing_comma_split(mock_ipinfo_response):
    """13. ipinfo.io 'loc': '52.5200,13.4050' parsed properly."""
    custom_ipinfo = dict(mock_ipinfo_response)
    custom_ipinfo["loc"] = "52.5200,13.4050"

    def _mock_urlopen(req, *args, **kwargs):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if "ip-api.com" in url:
            raise urllib.error.HTTPError(url, 500, "Error", {}, None)
        return _make_response(custom_ipinfo)

    with patch("urllib.request.urlopen", side_effect=_mock_urlopen):
        geo = detect_geo()

    assert geo.latitude == 52.52
    assert geo.longitude == 13.405


def test_geo_negative_coordinates_preservation():
    """14. Negative coordinates in southern/western hemispheres preserved."""
    sydney_resp = {
        "status": "success",
        "country": "Australia",
        "countryCode": "AU",
        "city": "Sydney",
        "lat": -33.8688,
        "lon": 151.2093,
        "isp": "Telstra",
        "org": "Telstra Corporation",
        "as": "AS1221 Telstra",
        "query": "1.1.1.1",
    }
    with patch("urllib.request.urlopen", return_value=_make_response(sydney_resp)):
        geo = detect_geo()

    assert geo.latitude == -33.8688
    assert geo.longitude == 151.2093
    assert geo.country_code == "AU"


def test_primary_ip_api_success(mock_ip_api_response: Dict[str, Any]) -> None:
    """15. Detector class: Successful detection using primary provider ip-api.com."""
    detector = GeoDetector()
    mock_resp = _make_response(mock_ip_api_response)

    with patch("urllib.request.urlopen", return_value=mock_resp):
        info = detector.get_geo_info(force_refresh=True)

    assert info["ip"] == "84.116.142.50"
    assert info["isp"] == "Deutsche Telekom AG"
    assert info["asn"] == "AS3320"
    assert info["country_code"] == "DE"
    assert info["latitude"] == 50.1109
    assert info["longitude"] == 8.6821


def test_fallback_to_ipinfo_when_primary_fails(mock_ipinfo_response: Dict[str, Any]) -> None:
    """16. Detector class: Fallback to ipinfo.io when ip-api.com fails."""
    detector = GeoDetector()

    def urlopen_side_effect(req, *args, **kwargs):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if "ip-api.com" in url:
            raise Exception("HTTP 500 Server Error")
        elif "ipinfo.io" in url:
            return _make_response(mock_ipinfo_response)
        raise Exception(f"Unexpected URL: {url}")

    with patch("urllib.request.urlopen", side_effect=urlopen_side_effect):
        info = detector.get_geo_info(force_refresh=True)

    assert info["ip"] == "84.116.142.50"
    assert info["country_code"] == "DE"
    assert info["latitude"] == 50.1109
    assert info["longitude"] == 8.6821


def test_in_memory_cache_prevents_duplicate_requests(mock_ip_api_response: Dict[str, Any]) -> None:
    """17. Detector class: in-memory cache avoids repeated network queries within TTL window."""
    detector = GeoDetector()
    mock_resp = _make_response(mock_ip_api_response)

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        info1 = detector.get_geo_info()
        info2 = detector.get_geo_info()

    assert mock_urlopen.call_count == 1
    assert info1 == info2


def test_force_refresh_bypasses_cache_detector(mock_ip_api_response: Dict[str, Any]) -> None:
    """18. Detector class: force_refresh=True triggers new query despite cached data."""
    detector = GeoDetector()
    mock_resp = _make_response(mock_ip_api_response)

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        detector.get_geo_info()
        detector.get_geo_info(force_refresh=True)

    assert mock_urlopen.call_count == 2


def test_resilience_when_all_geo_providers_fail() -> None:
    """19. Detector class: Complete blackout gracefully falls back to default GeoInfo without crashing."""
    detector = GeoDetector()
    with patch("urllib.request.urlopen", side_effect=Exception("Total network partition")):
        info = detector.get_geo_info(force_refresh=True)

    assert info["ip"] == "0.0.0.0"
    assert info["country_code"] == "UNKNOWN"
    assert info["latitude"] == 0.0
    assert info["longitude"] == 0.0


def test_module_level_detect_geo(mock_ip_api_response: Dict[str, Any]) -> None:
    """20. Module-level helper function detect_geo()."""
    clear_cache()
    mock_resp = _make_response(mock_ip_api_response)

    with patch("urllib.request.urlopen", return_value=mock_resp):
        info = detect_geo(checker_name="Test-Node", force_refresh=True)

    assert info["name"] == "Test-Node"
    assert info["country_code"] == "DE"
