"""Unit tests for the embedded xray-checker supervisor subsystem."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import MagicMock, patch
import pytest

from checker.supervisor import BinaryNotFoundError, SupervisorError, XraySupervisor


@pytest.fixture
def supervisor():
    return XraySupervisor(
        subscription_url="https://sub.url/token",
        polling_interval=30,
        host="127.0.0.1",
        port=2112,
        proxy_timeout=15,
        log_level="INFO",
    )


def test_supervisor_env_construction(supervisor: XraySupervisor):
    """Verifies environment variables passed to child xray-checker process."""
    env = supervisor._build_env()
    assert env["SUBSCRIPTION_URL"] == "https://sub.url/token"
    assert env["SUBSCRIPTION_UPDATE"] == "true"
    assert env["SUBSCRIPTION_UPDATE_INTERVAL"] == "30"
    assert env["PROXY_CHECK_INTERVAL"] == "30"
    assert env["METRICS_HOST"] == "127.0.0.1"
    assert env["METRICS_PORT"] == "2112"
    assert env["METRICS_PROTECTED"] == "false"
    assert env["PROXY_TIMEOUT"] == "15"
    assert env["LOG_LEVEL"] == "info"


def test_supervisor_binary_resolution():
    """Verifies binary resolution checks paths and environment variable overrides."""
    with patch.dict(os.environ, {"XRAY_CHECKER_BINARY": "/custom/bin/xray-checker"}):
        with patch("pathlib.Path.is_file", return_value=True):
            resolved = XraySupervisor._resolve_binary_path()
            assert "xray-checker" in resolved


def test_supervisor_start_and_drain(supervisor: XraySupervisor):
    """Verifies child process spawning and output drain thread creation."""
    mock_proc = MagicMock()
    mock_proc.pid = 9999
    mock_proc.poll.return_value = None
    mock_stdout = MagicMock()
    mock_stdout.readline.side_effect = ["Starting xray-checker\n", "Listening on 127.0.0.1:2112\n", ""]
    mock_proc.stdout = mock_stdout

    with patch("subprocess.Popen", return_value=mock_proc) as mock_popen:
        supervisor.start()
        assert supervisor.process is not None
        assert supervisor.process.pid == 9999
        assert mock_popen.call_count == 1
        assert supervisor._output_thread is not None
        assert supervisor._output_thread.is_alive() or supervisor._output_thread.name == "xray-checker-pipe-drain"

    supervisor.stop()


def test_supervisor_wait_until_ready_success(supervisor: XraySupervisor):
    """Verifies that wait_until_ready succeeds when /api/v1/status responds 200."""
    supervisor.process = MagicMock()
    supervisor.process.poll.return_value = None

    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        ready = supervisor.wait_until_ready(timeout_sec=5.0)
    assert ready is True


def test_supervisor_wait_until_ready_timeout(supervisor: XraySupervisor):
    """Verifies TimeoutError when endpoint does not respond before deadline."""
    supervisor.process = MagicMock()
    supervisor.process.poll.return_value = None

    with patch("urllib.request.urlopen", side_effect=Exception("Connection refused")):
        with pytest.raises(TimeoutError):
            supervisor.wait_until_ready(timeout_sec=0.1, poll_interval=0.02)


def test_supervisor_is_healthy(supervisor: XraySupervisor):
    """Verifies health check when process is running and responding."""
    supervisor.process = MagicMock()
    supervisor.process.poll.return_value = None

    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        assert supervisor.is_healthy() is True

    # When process exited
    supervisor.process.poll.return_value = 1
    assert supervisor.is_healthy() is False


def test_supervisor_restart_if_needed(supervisor: XraySupervisor):
    """Verifies that restart_if_needed restarts crashed process."""
    supervisor.process = MagicMock()
    supervisor.process.poll.return_value = 1  # Process crashed

    with patch.object(supervisor, "stop") as mock_stop, \
         patch.object(supervisor, "start") as mock_start, \
         patch.object(supervisor, "wait_until_ready") as mock_wait:
        restarted = supervisor.restart_if_needed()
        assert restarted is True
        assert mock_stop.call_count == 1
        assert mock_start.call_count == 1
        assert mock_wait.call_count == 1


def test_supervisor_stop_graceful_and_kill(supervisor: XraySupervisor):
    """Verifies terminate call and SIGKILL escalation if process fails to terminate."""
    mock_proc = MagicMock()
    mock_proc.pid = 1234
    mock_proc.poll.return_value = None
    mock_proc.wait.side_effect = [subprocess.TimeoutExpired(cmd="xray", timeout=5), None]

    supervisor.process = mock_proc
    supervisor.stop(timeout_sec=0.05)

    assert mock_proc.terminate.call_count == 1
    assert mock_proc.kill.call_count == 1
