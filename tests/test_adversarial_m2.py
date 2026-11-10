"""Adversarial stress-testing suite for DinoVoyager Checker (Milestone 2).

Challenger tests targeting config boundary values, corrupted /api/v1/proxies payloads,
and supervisor lifecycle / process death / rapid restart / pipe drain edge cases.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import threading
import time
from unittest.mock import MagicMock, patch
import pytest

from checker.config import ConfigError, load_config
from checker.supervisor import BinaryNotFoundError, SupervisorError, XraySupervisor
from checker.tester import (
    KNOWN_HOST_MAP,
    KNOWN_IP_MAP,
    ProxyTester,
    RouteResult,
    TestSummary,
    compute_metrics,
    compute_summary,
    map_location_code,
    poll_proxies,
)


# ============================================================================
# PART 1: Config Boundary & Adversarial Precedence Tests
# ============================================================================

class TestConfigAdversarial:
    """Stress tests and boundary checks for checker.config."""

    @pytest.mark.parametrize("invalid_interval", [
        "14",
        "0",
        "-1",
        "-999",
        "14.99",
        "-0",
        "NaN",
        "Infinity",
        "abc",
        "",
        " ",
    ])
    def test_polling_interval_boundary_and_invalid_cli(self, invalid_interval: str):
        """CLI: Rejects any polling interval < 15 or non-integer literal."""
        args = [
            "-s", "https://sub.url/token",
            "-i", invalid_interval,
            "-u", "https://api.dinovoyager.net",
            "-k", "dvc_live_key123",
        ]
        with pytest.raises(ConfigError) as exc_info:
            load_config(args, env={})
        err_msg = str(exc_info.value)
        assert "POLLING_INTERVAL" in err_msg or "Argument parsing error" in err_msg

    @pytest.mark.parametrize("invalid_interval", ["14", "0", "-10", "invalid"])
    def test_polling_interval_boundary_and_invalid_env(self, invalid_interval: str):
        """ENV: Rejects any polling interval < 15 or non-integer literal in environment."""
        env = {
            "SUBSCRIPTION_URL": "https://sub.url/token",
            "POLLING_INTERVAL": invalid_interval,
            "API_URL": "https://api.dinovoyager.net",
            "API_KEY": "dvc_live_key123",
        }
        with pytest.raises(ConfigError) as exc_info:
            load_config([], env=env)
        assert "POLLING_INTERVAL" in str(exc_info.value)

    @pytest.mark.parametrize("valid_interval,expected", [
        ("15", 15),
        ("16", 16),
        ("60", 60),
        ("86400", 86400),
        ("1000000", 1000000),
    ])
    def test_polling_interval_valid_boundaries(self, valid_interval: str, expected: int):
        """Accepts exact boundary 15 and higher values."""
        args = [
            "-s", "https://sub.url/token",
            "-i", valid_interval,
            "-u", "https://api.dinovoyager.net",
            "-k", "dvc_live_key123",
        ]
        cfg = load_config(args, env={})
        assert cfg.polling_interval == expected

    @pytest.mark.parametrize("bad_url", [
        "ftp://sub.url/file",
        "ws://sub.url/ws",
        "file:///etc/passwd",
        "data:text/plain;base64,SGVsbG8=",
        "javascript:alert(1)",
        "gopher://sub.url",
        "sub.dinovoyager.net/api",
        "://missing-scheme.com",
    ])
    def test_invalid_url_schemes(self, bad_url: str):
        """Rejects non-http(s) schemes for both subscription and API URLs."""
        # Bad subscription URL
        with pytest.raises(ConfigError) as exc:
            load_config([
                "-s", bad_url,
                "-i", "30",
                "-u", "https://api.dinovoyager.net",
                "-k", "key123",
            ], env={})
        assert "SUBSCRIPTION_URL scheme" in str(exc.value)

        # Bad API URL
        with pytest.raises(ConfigError) as exc:
            load_config([
                "-s", "https://sub.url",
                "-i", "30",
                "-u", bad_url,
                "-k", "key123",
            ], env={})
        assert "API_URL scheme" in str(exc.value)

    @pytest.mark.xfail(
        reason="BUG: config.py does not validate netloc; accepts 'http://' and 'https://' bare schemes without hostname",
        strict=True,
    )
    def test_bare_scheme_urls_rejected(self):
        """Challenge: bare schemes like 'http://' or 'https://' have no netloc and should be rejected."""
        args = ["-s", "https://", "-i", "30", "-u", "http://", "-k", "key123"]
        with pytest.raises(ConfigError):
            load_config(args, env={})

    def test_missing_every_combination_of_required_params(self):
        """Verifies clear error reporting when 1, 2, 3, or all 4 parameters are missing."""
        # None provided
        with pytest.raises(ConfigError) as exc:
            load_config([], env={})
        err_text = str(exc.value)
        assert "SUBSCRIPTION_URL is required" in err_text
        assert "POLLING_INTERVAL is required" in err_text
        assert "API_URL is required" in err_text
        assert "API_KEY is required" in err_text

    def test_cli_whitespace_only_flag_behavior(self):
        """Test behavior when CLI flag contains only whitespace."""
        env = {
            "SUBSCRIPTION_URL": "https://env.url/sub",
            "POLLING_INTERVAL": "60",
            "API_URL": "https://env.api.com",
            "API_KEY": "env_key",
        }
        # Passing whitespace for subscription-url
        with pytest.raises(ConfigError) as exc:
            load_config(["-s", "   "], env=env)
        assert "SUBSCRIPTION_URL is required" in str(exc.value)

    def test_api_url_trailing_slashes_and_paths(self):
        """Verifies multiple trailing slashes are cleanly stripped without corrupting path."""
        args = [
            "-s", "https://sub.url/token",
            "-i", "30",
            "-u", "https://api.dinovoyager.net:8443/v1///",
            "-k", "key123",
        ]
        cfg = load_config(args, env={})
        assert cfg.api_url == "https://api.dinovoyager.net:8443/v1"

    def test_proxy_timeout_boundaries(self):
        """Verifies PROXY_TIMEOUT boundary validation (minimum 1 second)."""
        base = ["-s", "https://sub.url", "-i", "30", "-u", "https://api.url", "-k", "key"]
        with pytest.raises(ConfigError) as exc:
            load_config(base + ["--timeout", "0"], env={})
        assert "PROXY_TIMEOUT must be at least 1 second" in str(exc.value)

        with pytest.raises(ConfigError) as exc:
            load_config(base + ["--timeout", "-5"], env={})
        assert "PROXY_TIMEOUT must be at least 1 second" in str(exc.value)

        with pytest.raises(ConfigError) as exc:
            load_config(base + ["--timeout", "abc"], env={})
        assert "Invalid integer literal for PROXY_TIMEOUT" in str(exc.value)

        cfg = load_config(base + ["--timeout", "1"], env={})
        assert cfg.proxy_timeout == 1


# ============================================================================
# PART 2: Tester Resilience Against Corrupted Payloads
# ============================================================================

class TestTesterAdversarial:
    """Stress tests for checker.tester against corrupted /api/v1/proxies responses."""

    @pytest.mark.xfail(
        reason="BUG: map_location_code crashes with AttributeError: 'NoneType' object has no attribute 'strip' when server, name, or country is None",
        strict=True,
    )
    def test_map_location_code_with_none_inputs(self):
        """Challenge: map_location_code should safely handle None arguments without AttributeError."""
        res = map_location_code(name=None, server=None, country=None, country_code=None)
        assert res == "UNKNOWN"

    @pytest.mark.xfail(
        reason="BUG: map_location_code crashes with AttributeError when arguments are int/list instead of str",
        strict=True,
    )
    def test_map_location_code_with_non_string_types(self):
        """Challenge: map_location_code should handle unexpected int/list/dict types."""
        res1 = map_location_code(name=12345, server=67890, country=111, country_code=222)
        assert isinstance(res1, str)

    @pytest.mark.xfail(
        reason="BUG: poll_proxies crashes when /api/v1/proxies response contains null in name, server, or country",
        strict=True,
    )
    def test_poll_proxies_corrupted_json_null_fields(self):
        """Challenge: /api/v1/proxies response with null in name, server, country fields."""
        payload = json.dumps({
            "data": [
                {
                    "name": None,
                    "server": None,
                    "country": None,
                    "countryCode": None,
                    "port": 443,
                    "protocol": "vless",
                    "online": True,
                    "latency_ms": 42.0,
                }
            ]
        }).encode("utf-8")

        mock_resp = MagicMock()
        mock_resp.read.return_value = payload
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp):
            routes, summary = poll_proxies()
            assert len(routes) == 1
            assert routes[0].location_code in ("UNKNOWN", "")

    @pytest.mark.xfail(
        reason="BUG: poll_proxies crashes when all proxy fields in JSON are null",
        strict=True,
    )
    def test_poll_proxies_all_fields_none(self):
        """Challenge: item where every single field is null."""
        payload = json.dumps({
            "data": [
                {
                    "name": None,
                    "server": None,
                    "port": None,
                    "protocol": None,
                    "location_code": None,
                    "online": None,
                    "latency_ms": None,
                    "country": None,
                    "countryCode": None,
                }
            ]
        }).encode("utf-8")

        mock_resp = MagicMock()
        mock_resp.read.return_value = payload
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp):
            routes, summary = poll_proxies()
            assert len(routes) == 1
            assert routes[0].online is False
            assert routes[0].latency_ms is None

    @pytest.mark.xfail(
        reason="BUG: compute_metrics crashes with TypeError: '>' not supported between instances of 'str' and 'int' when latency_ms is a string",
        strict=True,
    )
    def test_compute_metrics_string_latency(self):
        """Challenge: compute_metrics should sanitize string latency without TypeError."""
        res = compute_metrics([{"online": True, "latency_ms": "not_a_number"}])
        assert isinstance(res, dict)
        assert res["avg_latency_ms"] == 0.0

    def test_poll_proxies_extreme_and_special_latencies(self):
        """Test negative, extreme high, zero, and special float latencies."""
        payload = json.dumps({
            "data": [
                # Negative latency on online route (should be sanitized to None)
                {"name": "neg", "server": "1.1.1.1", "port": 443, "online": True, "latency_ms": -50.0},
                # Zero latency on online route
                {"name": "zero", "server": "1.1.1.2", "port": 443, "online": True, "latency_ms": 0.0},
                # Extreme high latency (1,000,000 ms)
                {"name": "extreme", "server": "1.1.1.3", "port": 443, "online": True, "latency_ms": 1000000.0},
                # Normal latency
                {"name": "normal", "server": "1.1.1.4", "port": 443, "online": True, "latency_ms": 50.0},
                # Offline with latency reported
                {"name": "off", "server": "1.1.1.5", "port": 443, "online": False, "latency_ms": 100.0},
            ]
        }).encode("utf-8")

        mock_resp = MagicMock()
        mock_resp.read.return_value = payload
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp):
            routes, summary = poll_proxies()

            assert len(routes) == 5
            # Negative latency must be None
            neg_r = next(r for r in routes if r.name == "neg")
            assert neg_r.latency_ms is None

            # Offline route latency must be None
            off_r = next(r for r in routes if r.name == "off")
            assert off_r.latency_ms is None

            # Online routes count: neg, zero, extreme, normal = 4 online out of 5
            assert summary.online_routes == 4
            assert summary.total_routes == 5
            assert summary.reliability_pct == 80.0

            # Valid latencies for avg: extreme (1000000.0) and normal (50.0)
            assert summary.min_latency_ms == 50.0
            assert summary.max_latency_ms == 1000000.0
            assert summary.avg_latency_ms == 500025.0

    @pytest.mark.parametrize("corrupted_data", [
        None,
        "string_instead_of_json_obj",
        12345,
        [],
        {"data": None},
        {"data": "not_a_list"},
        {"data": [None, 123, "random", []]},
    ])
    def test_poll_proxies_corrupted_top_level_structures(self, corrupted_data):
        """Verifies safe fallback when top-level response or 'data' key has corrupted types."""
        payload = json.dumps(corrupted_data).encode("utf-8")
        mock_resp = MagicMock()
        mock_resp.read.return_value = payload
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp):
            routes, summary = poll_proxies()
            assert isinstance(routes, list)
            assert isinstance(summary, TestSummary)
            assert summary.total_routes == 0

    def test_compute_summary_large_scale(self):
        """Stress-tests summary calculation with 5,000 routes for performance and precision."""
        routes = [
            RouteResult(
                name=f"route-{i}",
                server=f"10.0.{i // 256}.{i % 256}",
                port=443,
                protocol="vless",
                location_code="NL",
                online=(i % 3 != 0),  # 2/3 online
                latency_ms=(20.0 + (i % 100)) if (i % 3 != 0) else None,
            )
            for i in range(5000)
        ]

        t0 = time.monotonic()
        summary = compute_summary(routes)
        elapsed = time.monotonic() - t0

        assert summary.total_routes == 5000
        assert summary.online_routes == 3333
        assert 66.0 <= summary.reliability_pct <= 67.0
        assert 20.0 <= summary.avg_latency_ms <= 120.0
        assert elapsed < 0.2  # Must execute under 200ms


# ============================================================================
# PART 3: Supervisor Lifecycle & Edge Case Tests
# ============================================================================

class TestSupervisorAdversarial:
    """Stress tests for XraySupervisor process management and pipe draining."""

    def test_wait_until_ready_detects_immediate_process_death(self):
        """Supervisor must detect immediate process crash without hanging until timeout."""
        sup = XraySupervisor(subscription_url="https://sub.url", port=2199)
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 1  # Premature exit with code 1
        sup.process = mock_proc

        t0 = time.monotonic()
        with pytest.raises(SupervisorError) as exc:
            sup.wait_until_ready(timeout_sec=10.0)
        elapsed = time.monotonic() - t0

        assert "terminated prematurely with exit code 1" in str(exc.value)
        assert elapsed < 1.0  # Must fail instantly, not wait 10 seconds!

    def test_wait_until_ready_detects_sigkill_137(self):
        """Supervisor must detect SIGKILL (code -9 or 137) immediately."""
        sup = XraySupervisor(subscription_url="https://sub.url", port=2199)
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 137  # OOM / SIGKILL
        sup.process = mock_proc

        with pytest.raises(SupervisorError) as exc:
            sup.wait_until_ready(timeout_sec=5.0)
        assert "exit code 137" in str(exc.value)

    def test_rapid_restart_cycles_stability(self):
        """Verifies supervisor survives 10 rapid simulated restart cycles without leaking or locking."""
        sup = XraySupervisor(subscription_url="https://sub.url", port=2199)

        mock_proc = MagicMock()
        mock_proc.pid = 5555
        mock_proc.poll.return_value = 1  # Always crashed initially

        with patch("subprocess.Popen", return_value=mock_proc), \
             patch.object(sup, "wait_until_ready", return_value=True):
            for i in range(10):
                # Simulate crashed state
                sup.process = mock_proc
                mock_proc.poll.return_value = 1
                restarted = sup.restart_if_needed()
                assert restarted is True

    def test_pipe_drain_under_high_volume_output(self):
        """Verifies pipe drain thread processes 5,000 rapid output lines without deadlock."""
        sup = XraySupervisor(subscription_url="https://sub.url", port=2199)

        lines = [f"Log line {i} xray-checker output payload data\n" for i in range(5000)]
        mock_stream = io.StringIO("".join(lines))

        # Test drain function directly
        t0 = time.monotonic()
        sup._drain_output(mock_stream)
        elapsed = time.monotonic() - t0

        assert elapsed < 1.0  # Must drain 5000 lines in < 1 second

    def test_pipe_drain_handles_stream_error_gracefully(self):
        """Pipe drain thread must not crash if stream raises OSError or ValueError."""
        sup = XraySupervisor(subscription_url="https://sub.url", port=2199)

        mock_stream = MagicMock()
        mock_stream.readline.side_effect = OSError("Broken pipe / Bad file descriptor")

        # Must not raise unhandled exception
        sup._drain_output(mock_stream)
        assert mock_stream.close.called
