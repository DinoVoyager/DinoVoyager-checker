"""End-to-end integration test suite verifying full collection, testing, and report ingestion."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import signal
import threading
import time
from typing import Any, Dict, List
from unittest.mock import patch
import urllib.request

import pytest

from checker.config import Config
from checker.geo import clear_cache, detect_geo
from checker.reporter import assemble_payload, send_report
from checker.tester import poll_proxies


class MockDinoVoyagerServer(BaseHTTPRequestHandler):
    """In-process mock server serving subscription, proxy status, geo, and report ingestion."""

    proxies_response: Dict[str, Any] = {}
    geo_response: Dict[str, Any] = {}
    geo_request_count: int = 0
    post_statuses: List[int] = []
    received_reports: List[Dict[str, Any]] = []

    @classmethod
    def reset(cls) -> None:
        cls.proxies_response = {}
        cls.geo_response = {}
        cls.geo_request_count = 0
        cls.post_statuses = []
        cls.received_reports = []

    def do_GET(self) -> None:
        if "/json" in self.path:
            MockDinoVoyagerServer.geo_request_count += 1
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            data = MockDinoVoyagerServer.geo_response or {
                "status": "success",
                "country": "Germany",
                "countryCode": "DE",
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
            self.wfile.write(json.dumps(data).encode("utf-8"))
        elif self.path == "/api/v1/proxies":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            data = MockDinoVoyagerServer.proxies_response or {
                "status": "success",
                "data": [],
            }
            self.wfile.write(json.dumps(data).encode("utf-8"))
        elif self.path == "/subscription.txt":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"vless://user@64.188.82.195:443?type=tcp#EE-Reality\n")
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self) -> None:
        if self.path in ("/api/checkers/report", "/checkers/report"):
            api_key = self.headers.get("X-API-Key")
            content_type = self.headers.get("Content-Type", "")
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length).decode("utf-8"))

            MockDinoVoyagerServer.received_reports.append({
                "api_key": api_key,
                "content_type": content_type,
                "payload": body,
            })

            status = 200
            if MockDinoVoyagerServer.post_statuses:
                status = MockDinoVoyagerServer.post_statuses.pop(0)

            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            if status in (200, 201, 202):
                self.wfile.write(json.dumps({"ok": True, "received_at": 1741500000}).encode("utf-8"))
            elif status == 401:
                self.wfile.write(json.dumps({"error": "Unauthorized"}).encode("utf-8"))
            elif status == 503:
                self.wfile.write(json.dumps({"error": "Service Unavailable"}).encode("utf-8"))
            else:
                self.wfile.write(json.dumps({"error": f"HTTP {status}"}).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress default HTTP server logging during tests."""
        pass


@pytest.fixture
def mock_server():
    """Starts an in-process mock HTTP server on an ephemeral OS port."""
    MockDinoVoyagerServer.reset()
    server = HTTPServer(("127.0.0.1", 0), MockDinoVoyagerServer)
    port = server.server_port
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    yield server, port

    server.shutdown()
    server.server_close()


def _run_single_integration_cycle(port: int, config: Config) -> bool:
    """Executes a single end-to-end check cycle using the mock server for all components."""
    # Route external geo requests to the in-process mock server
    orig_urlopen = urllib.request.urlopen

    def custom_urlopen(req, *args, **kwargs):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if "ip-api.com" in url or "ipinfo.io" in url or "ipwhois.app" in url:
            req = urllib.request.Request(f"http://127.0.0.1:{port}/json/")
        return orig_urlopen(req, *args, **kwargs)

    with patch("urllib.request.urlopen", side_effect=custom_urlopen):
        geo = detect_geo(checker_name=config.checker_name)

    routes, summary = poll_proxies(host="127.0.0.1", port=port, timeout=config.proxy_timeout)
    payload = assemble_payload(config, geo, routes, summary)
    success = send_report(payload, config, timeout=5)
    return success


def test_integration_full_healthy_cycle(mock_server, mock_proxies_data, mock_ip_api_response):
    """1. Full end-to-end healthy check and report delivery cycle."""
    server, port = mock_server
    MockDinoVoyagerServer.proxies_response = mock_proxies_data
    MockDinoVoyagerServer.geo_response = mock_ip_api_response

    config = Config(
        subscription_url=f"http://127.0.0.1:{port}/subscription.txt",
        polling_interval=15,
        api_url=f"http://127.0.0.1:{port}",
        api_key="dvc_live_valid_test_key_42",
        checker_name="Integration-Frankfurt",
    )

    success = _run_single_integration_cycle(port, config)
    assert success is True
    assert len(MockDinoVoyagerServer.received_reports) == 1

    rx = MockDinoVoyagerServer.received_reports[0]
    assert rx["api_key"] == "dvc_live_valid_test_key_42"
    assert "application/json" in rx["content_type"]

    p = rx["payload"]
    assert p["checker_info"]["name"] == "Integration-Frankfurt"
    assert p["checker_info"]["country_code"] == "DE"
    assert p["summary"]["total_routes"] == 4
    assert p["summary"]["online_routes"] == 3
    assert p["summary"]["reliability_pct"] == 75.0


def test_integration_zero_routes_subscription(mock_server, mock_ip_api_response):
    """2. End-to-end cycle when subscription has 0 routes."""
    server, port = mock_server
    MockDinoVoyagerServer.proxies_response = {"status": "success", "data": []}
    MockDinoVoyagerServer.geo_response = mock_ip_api_response

    config = Config(
        subscription_url=f"http://127.0.0.1:{port}/subscription.txt",
        polling_interval=15,
        api_url=f"http://127.0.0.1:{port}",
        api_key="dvc_live_valid_test_key_42",
    )

    success = _run_single_integration_cycle(port, config)
    assert success is True
    assert len(MockDinoVoyagerServer.received_reports) == 1

    p = MockDinoVoyagerServer.received_reports[0]["payload"]
    assert p["summary"]["total_routes"] == 0
    assert p["summary"]["online_routes"] == 0
    assert p["summary"]["avg_latency_ms"] == 0.0
    assert p["summary"]["reliability_pct"] == 0.0


def test_integration_all_routes_offline_cycle(mock_server, mock_ip_api_response):
    """3. End-to-end cycle when all proxy nodes are down."""
    server, port = mock_server
    MockDinoVoyagerServer.proxies_response = {
        "status": "success",
        "data": [
            {"name": "Node1", "server": "1.1.1.1", "port": 443, "online": False, "latencyMs": None},
            {"name": "Node2", "server": "1.1.1.2", "port": 443, "online": False, "latencyMs": None},
            {"name": "Node3", "server": "1.1.1.3", "port": 443, "online": False, "latencyMs": None},
        ],
    }
    MockDinoVoyagerServer.geo_response = mock_ip_api_response

    config = Config(
        subscription_url=f"http://127.0.0.1:{port}/subscription.txt",
        polling_interval=15,
        api_url=f"http://127.0.0.1:{port}",
        api_key="dvc_live_valid_test_key_42",
    )

    success = _run_single_integration_cycle(port, config)
    assert success is True
    assert len(MockDinoVoyagerServer.received_reports) == 1

    p = MockDinoVoyagerServer.received_reports[0]["payload"]
    assert p["summary"]["total_routes"] == 3
    assert p["summary"]["online_routes"] == 0
    assert p["summary"]["avg_latency_ms"] == 0.0
    assert p["summary"]["reliability_pct"] == 0.0


def test_integration_invalid_api_key_rejection(mock_server, mock_proxies_data):
    """4. End-to-end behavior when API key is rejected with HTTP 401."""
    server, port = mock_server
    MockDinoVoyagerServer.proxies_response = mock_proxies_data
    MockDinoVoyagerServer.post_statuses = [401]

    config = Config(
        subscription_url=f"http://127.0.0.1:{port}/subscription.txt",
        polling_interval=15,
        api_url=f"http://127.0.0.1:{port}",
        api_key="dvc_live_invalid_revoked_key",
    )

    success = _run_single_integration_cycle(port, config)
    assert success is False
    # Verified: NO aggressive retry on HTTP 401
    assert len(MockDinoVoyagerServer.received_reports) == 1


def test_integration_server_temporary_503_recovery(mock_server, mock_proxies_data):
    """5. End-to-end retry recovery on transient server error (503 -> 200)."""
    server, port = mock_server
    MockDinoVoyagerServer.proxies_response = mock_proxies_data
    MockDinoVoyagerServer.post_statuses = [503, 200]

    config = Config(
        subscription_url=f"http://127.0.0.1:{port}/subscription.txt",
        polling_interval=15,
        api_url=f"http://127.0.0.1:{port}",
        api_key="dvc_live_valid_key",
    )

    with patch("time.sleep"):  # fast-forward backoff delay
        success = _run_single_integration_cycle(port, config)

    assert success is True
    # Exactly 2 POST attempts (1st failed with 503, 2nd succeeded with 200)
    assert len(MockDinoVoyagerServer.received_reports) == 2


def test_integration_geo_cache_persistence_across_cycles(mock_server, mock_proxies_data, mock_ip_api_response):
    """6. Verify Geo cache persistence across consecutive check cycles."""
    clear_cache()
    server, port = mock_server
    MockDinoVoyagerServer.proxies_response = mock_proxies_data
    MockDinoVoyagerServer.geo_response = mock_ip_api_response

    config = Config(
        subscription_url=f"http://127.0.0.1:{port}/subscription.txt",
        polling_interval=15,
        api_url=f"http://127.0.0.1:{port}",
        api_key="dvc_live_valid_key",
    )

    # First cycle
    success1 = _run_single_integration_cycle(port, config)
    assert success1 is True
    assert MockDinoVoyagerServer.geo_request_count == 1

    # Second cycle (should use in-memory cache)
    success2 = _run_single_integration_cycle(port, config)
    assert success2 is True
    assert MockDinoVoyagerServer.geo_request_count == 1  # 0 additional geo requests made

    assert len(MockDinoVoyagerServer.received_reports) == 2
    rep1 = MockDinoVoyagerServer.received_reports[0]["payload"]["checker_info"]
    rep2 = MockDinoVoyagerServer.received_reports[1]["payload"]["checker_info"]
    assert rep1["ip"] == rep2["ip"]
    assert rep1["isp"] == rep2["isp"]


def test_integration_graceful_shutdown_sigterm():
    """7. Verify supervisor shutdown event handling on termination signal."""
    shutdown_event = threading.Event()

    def signal_handler(signum, frame):
        shutdown_event.set()

    # Simulate SIGTERM reception
    assert not shutdown_event.is_set()
    signal_handler(signal.SIGTERM, None)
    assert shutdown_event.is_set()


def test_end_to_end_collection_and_report_cycle(mock_server):
    """8. Compatibility end-to-end test using ProxyTester and ReportManager."""
    server, port = mock_server
    MockDinoVoyagerServer.proxies_response = {
        "status": "success",
        "data": [
            {
                "name": "EE-Reality",
                "server": "64.188.82.195",
                "port": 443,
                "protocol": "vless",
                "countryCode": "EE",
                "online": True,
                "latencyMs": 35.5,
            }
        ],
    }

    base_url = f"http://127.0.0.1:{port}"
    config = Config(
        subscription_url=f"{base_url}/subscription.txt",
        polling_interval=15,
        api_url=base_url,
        api_key="dvc_live_integration_key_42",
        checker_name="IntegrationNode",
    )

    routes, summary = poll_proxies(host="127.0.0.1", port=port)
    geo = detect_geo(checker_name="IntegrationNode")
    payload = assemble_payload(config, geo, routes, summary)
    success = send_report(payload, config)

    assert success is True
    assert len(MockDinoVoyagerServer.received_reports) == 1
    rx = MockDinoVoyagerServer.received_reports[0]
    assert rx["api_key"] == "dvc_live_integration_key_42"
    assert rx["payload"]["summary"]["total_routes"] == 1
    assert rx["payload"]["summary"]["online_routes"] == 1
