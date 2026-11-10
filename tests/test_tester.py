"""Unit tests for checker.tester module (15 test cases)."""

import json
import socket
from unittest.mock import MagicMock, patch
import urllib.error

import pytest

from checker.tester import (
    RouteResult,
    TestSummary,
    compute_summary,
    map_location_code,
    poll_proxies,
)


def _make_response(data, status: int = 200):
    body = json.dumps(data).encode("utf-8") if isinstance(data, (dict, list)) else data
    resp = MagicMock()
    resp.read.return_value = body
    resp.status = status
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = None
    return resp


def test_tester_poll_proxies_success(mock_proxies_data):
    """1. Successful poll of local xray-checker on port 2112."""
    with patch("urllib.request.urlopen", return_value=_make_response(mock_proxies_data)):
        routes, summary = poll_proxies(host="127.0.0.1", port=2112)

    assert len(routes) == 4
    assert routes[0].name == "EE-VLESS-Reality"
    assert routes[0].server == "64.188.82.195"
    assert routes[0].port == 443
    assert routes[0].protocol == "vless"
    assert routes[0].location_code == "EE"
    assert routes[0].online is True
    assert routes[0].latency_ms == 35.0

    assert routes[3].name == "DE-VLESS-Reality"
    assert routes[3].online is False
    assert routes[3].latency_ms is None


def test_tester_location_mapping_ee():
    """2. Location mapping for Estonia identifiers."""
    assert map_location_code(name="EE-VLESS-Reality") == "EE"
    assert map_location_code(name="Сервер Эстония") == "EE"
    assert map_location_code(server="ee.dinovoyager.net") == "EE"
    assert map_location_code(server="64.188.82.195") == "EE"


def test_tester_location_mapping_nl():
    """3. Location mapping for Netherlands identifiers."""
    assert map_location_code(name="NL-VLESS") == "NL"
    assert map_location_code(name="Нидерланды Play2Go") == "NL"
    assert map_location_code(server="ne.dinovoyager.net") == "NL"
    assert map_location_code(server="2.27.40.72") == "NL"


def test_tester_location_mapping_fi():
    """4. Location mapping for Finland identifiers."""
    assert map_location_code(name="FI-Trojan") == "FI"
    assert map_location_code(name="Финляндия") == "FI"
    assert map_location_code(server="fl.dinovoyager.net") == "FI"
    assert map_location_code(server="191.217.252.52") == "FI"


def test_tester_location_mapping_de():
    """5. Location mapping for Germany identifiers."""
    assert map_location_code(name="DE-Shadowsocks") == "DE"
    assert map_location_code(name="Германия Франкфурт") == "DE"
    assert map_location_code(server="de.dinovoyager.net") == "DE"
    assert map_location_code(server="138.124.126.158") == "DE"


def test_tester_location_mapping_fallback():
    """6. Location mapping for unknown / arbitrary country codes."""
    assert map_location_code(name="Tokyo-Node", country_code="JP") == "JP"
    assert map_location_code(name="Tokyo-Node") == "UNKNOWN"
    assert map_location_code(name="US-Fast-Node") == "US"


def test_tester_summary_calculation_all_online():
    """7. Summary calculation when 100% of routes are online."""
    routes = [
        RouteResult("R1", "1.1.1.1", 443, "vless", "EE", True, 20.0),
        RouteResult("R2", "1.1.1.2", 443, "vless", "NL", True, 40.0),
        RouteResult("R3", "1.1.1.3", 443, "vless", "DE", True, 60.0),
    ]
    summary = compute_summary(routes)
    assert summary.total_routes == 3
    assert summary.online_routes == 3
    assert summary.avg_latency_ms == 40.0
    assert summary.min_latency_ms == 20.0
    assert summary.max_latency_ms == 60.0
    assert summary.reliability_pct == 100.0


def test_tester_summary_calculation_mixed_routes():
    """8. Summary calculation with both online and offline routes."""
    routes = [
        RouteResult("R1", "1.1.1.1", 443, "vless", "EE", True, 30.0),
        RouteResult("R2", "1.1.1.2", 443, "vless", "NL", False, 0.0),
        RouteResult("R3", "1.1.1.3", 443, "vless", "FI", True, 50.0),
        RouteResult("R4", "1.1.1.4", 443, "vless", "DE", False, None),
    ]
    summary = compute_summary(routes)
    assert summary.total_routes == 4
    assert summary.online_routes == 2
    assert summary.avg_latency_ms == 40.0
    assert summary.min_latency_ms == 30.0
    assert summary.max_latency_ms == 50.0
    assert summary.reliability_pct == 50.0


def test_tester_edge_zero_routes():
    """9. Empty proxies list computes zero statistics without division by zero."""
    summary = compute_summary([])
    assert summary.total_routes == 0
    assert summary.online_routes == 0
    assert summary.avg_latency_ms == 0.0
    assert summary.min_latency_ms == 0.0
    assert summary.max_latency_ms == 0.0
    assert summary.reliability_pct == 0.0


def test_tester_edge_all_routes_offline():
    """10. 100% routes offline yields 0% reliability and 0.0 latency."""
    routes = [
        RouteResult("R1", "1.1.1.1", 443, "vless", "EE", False, 0.0),
        RouteResult("R2", "1.1.1.2", 443, "vless", "NL", False, None),
        RouteResult("R3", "1.1.1.3", 443, "vless", "DE", False, 0.0),
    ]
    summary = compute_summary(routes)
    assert summary.total_routes == 3
    assert summary.online_routes == 0
    assert summary.avg_latency_ms == 0.0
    assert summary.reliability_pct == 0.0


def test_tester_edge_extreme_high_latency():
    """11. Extreme latency values computed accurately without integer overflow."""
    routes = [
        RouteResult("R1", "1.1.1.1", 443, "vless", "EE", True, 15000.0),
        RouteResult("R2", "1.1.1.2", 443, "vless", "NL", True, 45000.0),
    ]
    summary = compute_summary(routes)
    assert summary.avg_latency_ms == 30000.0
    assert summary.min_latency_ms == 15000.0
    assert summary.max_latency_ms == 45000.0


def test_tester_connection_refused_daemon_down():
    """12. Connection refused returns empty routes without crashing."""
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Connection refused")):
        routes, summary = poll_proxies()

    assert routes == []
    assert summary.total_routes == 0
    assert summary.online_routes == 0


def test_tester_timeout_during_poll():
    """13. Timeout polling xray-checker returns empty routes without crashing."""
    with patch("urllib.request.urlopen", side_effect=socket.timeout("Timed out")):
        routes, summary = poll_proxies()

    assert routes == []
    assert summary.total_routes == 0


def test_tester_malformed_json_response():
    """14. Malformed JSON returns empty routes gracefully."""
    with patch("urllib.request.urlopen", return_value=_make_response(b"{status: invalid")):
        routes, summary = poll_proxies()

    assert routes == []
    assert summary.total_routes == 0


def test_tester_missing_fields_in_proxy_item():
    """15. Proxy item missing optional fields uses sensible defaults without KeyError."""
    incomplete_data = {
        "status": "success",
        "data": [
            {
                "name": "Minimal-Node",
                "server": "1.2.3.4",
            }
        ],
    }
    with patch("urllib.request.urlopen", return_value=_make_response(incomplete_data)):
        routes, summary = poll_proxies()

    assert len(routes) == 1
    r = routes[0]
    assert r.name == "Minimal-Node"
    assert r.server == "1.2.3.4"
    assert r.port == 443
    assert r.protocol == "unknown"
    assert r.online is False
    assert r.latency_ms is None
