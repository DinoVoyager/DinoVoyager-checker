"""Shared pytest fixtures and mock factories for DinoVoyager Checker tests."""

from __future__ import annotations

import os
from typing import Any, Dict
import pytest

from checker.geo import clear_cache


@pytest.fixture(autouse=True)
def reset_geo_cache():
    """Ensure in-memory geo cache is reset before and after every test."""
    clear_cache()
    yield
    clear_cache()


@pytest.fixture(autouse=True)
def setup_test_location_maps():
    """Pre-populate location mapping for unit tests without external network."""
    from checker.tester import _dynamic_ip_map, _dynamic_host_map
    _dynamic_ip_map.clear()
    _dynamic_host_map.clear()
    _dynamic_ip_map.update({
        "64.188.82.195": "EE",
        "2.27.40.72": "NL",
        "191.217.252.52": "FI",
        "138.124.126.158": "DE",
    })
    _dynamic_host_map.update({
        "ee.dinovoyager.net": "EE",
        "ne.dinovoyager.net": "NL",
        "nl.dinovoyager.net": "NL",
        "fl.dinovoyager.net": "FI",
        "fi.dinovoyager.net": "FI",
        "de.dinovoyager.net": "DE",
    })
    yield
    _dynamic_ip_map.clear()
    _dynamic_host_map.clear()


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure no checker-related environment variables are set."""
    keys = [
        "SUBSCRIPTION_URL",
        "POLLING_INTERVAL",
        "API_URL",
        "API_KEY",
        "CHECKER_NAME",
        "LOG_LEVEL",
        "PROXY_TIMEOUT",
        "XRAY_CHECKER_BINARY",
        "XRAY_CHECKER_BIN",
    ]
    for key in keys:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def valid_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Populate minimal valid configuration in environment."""
    monkeypatch.setenv("SUBSCRIPTION_URL", "https://app.dinovoyager.net/api/sub/valid_token")
    monkeypatch.setenv("POLLING_INTERVAL", "60")
    monkeypatch.setenv("API_URL", "https://app.dinovoyager.net")
    monkeypatch.setenv("API_KEY", "dvc_live_testkey1234567890abcdef")


@pytest.fixture
def mock_ip_api_response() -> Dict[str, Any]:
    """Sample successful ip-api.com response payload."""
    return {
        "status": "success",
        "country": "Germany",
        "countryCode": "DE",
        "region": "HE",
        "regionName": "Hesse",
        "city": "Frankfurt am Main",
        "lat": 50.1109,
        "lon": 8.6821,
        "timezone": "Europe/Berlin",
        "isp": "Deutsche Telekom AG",
        "org": "Deutsche Telekom",
        "as": "AS3320 Deutsche Telekom AG",
        "query": "84.116.142.50",
    }


@pytest.fixture
def mock_ipinfo_response() -> Dict[str, Any]:
    """Sample successful ipinfo.io response payload."""
    return {
        "ip": "84.116.142.50",
        "city": "Frankfurt am Main",
        "region": "Hesse",
        "country": "DE",
        "loc": "50.1109,8.6821",
        "org": "AS3320 Deutsche Telekom AG",
        "timezone": "Europe/Berlin",
    }


@pytest.fixture
def mock_ipwhois_response() -> Dict[str, Any]:
    """Sample successful ipwhois.app response payload."""
    return {
        "ip": "84.116.142.50",
        "success": True,
        "country": "Germany",
        "country_code": "DE",
        "city": "Frankfurt am Main",
        "latitude": 50.1109,
        "longitude": 8.6821,
        "isp": "Deutsche Telekom AG",
        "org": "Deutsche Telekom",
        "asn": "AS3320",
    }


@pytest.fixture
def mock_proxies_data() -> Dict[str, Any]:
    """Canned proxy payload returned by xray-checker /api/v1/proxies conforming to spec miner."""
    return {
        "status": "success",
        "data": [
            {
                "name": "EE-VLESS-Reality",
                "server": "64.188.82.195",
                "port": 443,
                "protocol": "vless",
                "country": "Estonia",
                "countryCode": "EE",
                "online": True,
                "latencyMs": 35.0,
            },
            {
                "name": "NL-VLESS-Reality",
                "server": "2.27.40.72",
                "port": 443,
                "protocol": "vless",
                "country": "Netherlands",
                "countryCode": "NL",
                "online": True,
                "latencyMs": 20.0,
            },
            {
                "name": "FI-VLESS-Reality",
                "server": "191.217.252.52",
                "port": 443,
                "protocol": "vless",
                "country": "Finland",
                "countryCode": "FI",
                "online": True,
                "latencyMs": 45.0,
            },
            {
                "name": "DE-VLESS-Reality",
                "server": "138.124.126.158",
                "port": 443,
                "protocol": "vless",
                "country": "Germany",
                "countryCode": "DE",
                "online": False,
                "latencyMs": None,
            },
        ],
    }


@pytest.fixture
def mock_xray_proxies_data(mock_proxies_data) -> Dict[str, Any]:
    """Alias for mock_proxies_data."""
    return mock_proxies_data
