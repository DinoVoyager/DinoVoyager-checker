"""Process supervisor for the embedded xray-checker Go binary.

Manages the lifecycle, loopback isolation, background pipe draining,
readiness polling, crash auto-recovery, and graceful termination of xray-checker.
"""

from __future__ import annotations

import base64
import http.server
import logging
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import sys
import threading
import time
from typing import Any, Dict, Optional
import urllib.error
import urllib.request

logger = logging.getLogger("dinovoyager-checker.supervisor")
proc_logger = logging.getLogger("xray-checker")


class _SubscriptionFilterHandler(http.server.BaseHTTPRequestHandler):
    """Local HTTP handler that fetches upstream subscription and filters out invalid/balancer routes."""

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress standard HTTP request logging."""
        pass

    def do_GET(self) -> None:
        upstream_url = getattr(self.server, "upstream_url", "")
        if not upstream_url:
            self.send_response(400)
            self.end_headers()
            return

        try:
            forward_headers = {}
            for k, v in self.headers.items():
                if k.lower() not in ("host", "accept-encoding", "content-length"):
                    forward_headers[k] = v
            if "User-Agent" not in forward_headers:
                forward_headers["User-Agent"] = "Xray-Checker"
            if "X-Hwid" not in forward_headers:
                forward_headers["X-Hwid"] = "0JLQq9Ca0JvQrtCn0Jgg0JHQm9Cp0KLQrCBIV0lE"
                forward_headers["X-Device-Model"] = "Xray-Checker Pro Max"
                forward_headers["X-Device-Os"] = "CheckerOS"
                forward_headers["X-Ver-Os"] = "v1.3.1"

            req = urllib.request.Request(
                upstream_url,
                headers=forward_headers,
            )
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

            with urllib.request.urlopen(req, timeout=15.0, context=ctx) as resp:
                raw_data = resp.read()

            try:
                decoded = base64.b64decode(raw_data + b"==").decode("utf-8", errors="ignore")
            except Exception:
                decoded = raw_data.decode("utf-8", errors="ignore")

            clean_lines = []
            for line in decoded.splitlines():
                line = line.strip()
                if not line:
                    continue
                # Exclude port 1234, balancer shadowsocks, or virtual auto-select configs
                if (
                    ":1234" in line
                    or "Авто-выбор" in line
                    or "авто-выбор" in line
                    or "%D0%90%D0%B2%D1%82%D0%BE-%D0%B2%D1%8B%D0%B1%D0%BE%D1%80" in line
                    or "%28%D0%90%D0%B2%D1%82%D0%BE-%D0%B2%D1%8B%D0%B1%D0%BE%D1%80" in line
                ):
                    continue

                # Clean hysteria2 configs: strip Remnawave-specific client JSON 'fm=' (which conflicts with Xray finalmask)
                # and ensure alpn=h3 is specified for proper QUIC negotiation
                if line.startswith("hysteria2://") or line.startswith("hy2://"):
                    import re
                    line = re.sub(r'([?&])fm=[^&#]+(&)?', lambda m: m.group(1) if m.group(1) == '?' and not m.group(2) else ('&' if m.group(2) else ''), line)
                    line = line.replace('?&', '?').rstrip('?&')
                    if "alpn=" not in line:
                        sep = '&' if '?' in line else '?'
                        if '#' in line:
                            base_part, frag = line.split('#', 1)
                            line = f"{base_part}{sep}alpn=h3#{frag}"
                        else:
                            line = f"{line}{sep}alpn=h3"

                clean_lines.append(line)

            if clean_lines:
                filtered_body = base64.b64encode("\n".join(clean_lines).encode("utf-8"))
            else:
                filtered_body = raw_data

            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(filtered_body)))
            self.end_headers()
            self.wfile.write(filtered_body)
        except Exception as exc:
            logger.warning("SubscriptionFilterServer upstream error: %s", exc)
            self.send_response(502)
            self.end_headers()


class BinaryNotFoundError(FileNotFoundError):
    """Raised when the xray-checker executable binary cannot be located."""
    pass


class SupervisorError(RuntimeError):
    """Raised on supervisor lifecycle failures."""
    pass


class XraySupervisor:
    """Supervises the xray-checker background process."""

    def __init__(
        self,
        subscription_url: str,
        polling_interval: int = 300,
        host: str = "127.0.0.1",
        port: int = 2112,
        binary_path: Optional[str] = None,
        proxy_timeout: int = 10,
        log_level: str = "WARN",
        xray_log_level: str = "none",
    ) -> None:
        self.subscription_url = subscription_url
        self.polling_interval = max(15, int(polling_interval))
        self.host = host
        self.port = int(port)
        self.proxy_timeout = int(proxy_timeout)
        self.log_level = log_level.lower()
        self.xray_log_level = xray_log_level.lower()

        self.binary_path = self._resolve_binary_path(binary_path)
        self.base_url = f"http://{self.host}:{self.port}"

        self.process: Optional[subprocess.Popen] = None
        self._output_thread: Optional[threading.Thread] = None
        self._should_stop = False
        self._lock = threading.Lock()
        self._sub_filter_server: Optional[http.server.HTTPServer] = None
        self._sub_filter_port: Optional[int] = None

    def _start_sub_filter_server(self) -> Optional[int]:
        """Starts a background HTTP server on 127.0.0.1 serving filtered subscription."""
        try:
            server = http.server.HTTPServer(("127.0.0.1", 0), _SubscriptionFilterHandler)
            server.upstream_url = self.subscription_url
            port = int(server.server_port)
            t = threading.Thread(target=server.serve_forever, daemon=True, name="sub-filter-proxy")
            t.start()
            self._sub_filter_server = server
            self._sub_filter_port = port
            logger.info("Started internal subscription filter proxy on 127.0.0.1:%d", port)
            return port
        except Exception as exc:
            logger.warning("Could not start internal subscription filter proxy: %s", exc)
            return None

    def _stop_sub_filter_server(self) -> None:
        if self._sub_filter_server is not None:
            try:
                self._sub_filter_server.shutdown()
                self._sub_filter_server.server_close()
            except Exception:
                pass
            self._sub_filter_server = None
            self._sub_filter_port = None

    @staticmethod
    def _resolve_binary_path(explicit_path: Optional[str] = None) -> str:
        """Finds the xray-checker executable across standard candidate paths."""
        candidates = []
        if explicit_path:
            candidates.append(Path(explicit_path))

        env_bin = os.environ.get("XRAY_CHECKER_BINARY") or os.environ.get("XRAY_CHECKER_BIN")
        if env_bin:
            candidates.append(Path(env_bin))

        # Container path
        candidates.append(Path("/usr/bin/xray-checker"))

        # Repo bin path
        repo_root = Path(__file__).resolve().parent.parent
        candidates.append(repo_root / "bin" / "xray-checker")
        candidates.append(repo_root / "bin" / "xray-checker.exe")

        for p in candidates:
            if p.is_file():
                return str(p.resolve())

        # Check system PATH
        which_path = shutil.which("xray-checker")
        if which_path:
            return which_path

        # If in unit test context on Windows, allow fallback path without raising if mock will be used
        if sys.platform == "win32" and "pytest" in sys.modules:
            fallback = repo_root / "bin" / "xray-checker"
            return str(fallback)

        raise BinaryNotFoundError(
            f"xray-checker binary not found. Checked: {[str(c) for c in candidates]}"
        )

    def _build_env(self) -> Dict[str, str]:
        """Builds isolated environment variables for child xray-checker process."""
        env = os.environ.copy()
        effective_sub_url = self.subscription_url
        if hasattr(self, "_sub_filter_port") and self._sub_filter_port:
            effective_sub_url = f"http://{self.host}:{self._sub_filter_port}/sub"

        env.update({
            "SUBSCRIPTION_URL": effective_sub_url,
            "SUBSCRIPTION_UPDATE": "true",
            "SUBSCRIPTION_UPDATE_INTERVAL": str(self.polling_interval),
            "PROXY_CHECK_INTERVAL": str(self.polling_interval),
            "PROXY_CHECK_METHOD": "status",
            "PROXY_STATUS_CHECK_URL": os.getenv("PROXY_STATUS_CHECK_URL", "http://cp.cloudflare.com/generate_204"),
            "PROXY_IP_CHECK_URL": "https://icanhazip.com",
            "PROXY_TIMEOUT": str(self.proxy_timeout),
            "METRICS_HOST": self.host,
            "METRICS_PORT": str(self.port),
            "METRICS_PROTECTED": "false",
            "LOG_LEVEL": self.log_level,
            "XRAY_LOG_LEVEL": self.xray_log_level,
        })
        return env

    def _drain_output(self, stream: subprocess.PIPE) -> None:
        """Asynchronously drains child stdout/stderr to prevent OS pipe deadlocks."""
        try:
            for raw_line in iter(stream.readline, ""):
                if not raw_line:
                    break
                line = raw_line.rstrip()
                if line:
                    if self.log_level == "debug":
                        proc_logger.debug(line)
                    else:
                        proc_logger.info(line)
        except (ValueError, OSError):
            pass
        finally:
            try:
                stream.close()
            except Exception:
                pass

    def start(self) -> None:
        """Launches xray-checker as a child subprocess."""
        with self._lock:
            if self.process is not None and self.process.poll() is None:
                logger.warning("xray-checker is already running (PID %d)", self.process.pid)
                return

            self._should_stop = False
            if self._sub_filter_server is None:
                self._start_sub_filter_server()

            logger.info("Starting xray-checker from %s on %s:%d", self.binary_path, self.host, self.port)

            env = self._build_env()
            try:
                self.process = subprocess.Popen(
                    [self.binary_path],
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
            except OSError as exc:
                if sys.platform == "win32" and getattr(exc, "winerror", None) == 193:
                    raise SupervisorError(
                        "Cannot execute Linux ELF binary directly on Windows host. "
                        "Run inside Docker or mock the supervisor for tests."
                    ) from exc
                raise SupervisorError(f"Failed to spawn xray-checker: {exc}") from exc

            # Start pipe drain daemon thread
            self._output_thread = threading.Thread(
                target=self._drain_output,
                args=(self.process.stdout,),
                daemon=True,
                name="xray-checker-pipe-drain",
            )
            self._output_thread.start()

            logger.info("xray-checker child process spawned with PID %d", self.process.pid)

    def wait_until_ready(self, timeout_sec: float = 30.0, poll_interval: float = 0.5) -> bool:
        """Polls /api/v1/status until the local HTTP service is healthy."""
        deadline = time.monotonic() + timeout_sec
        url = f"{self.base_url}/api/v1/status"

        logger.info("Waiting for xray-checker readiness at %s (timeout: %.1fs)...", url, timeout_sec)

        while time.monotonic() < deadline:
            # Check for early exit of child process
            if self.process is not None:
                code = self.process.poll()
                if code is not None:
                    raise SupervisorError(f"xray-checker terminated prematurely with exit code {code}")

            try:
                req = urllib.request.Request(url, headers={"User-Agent": "DinoVoyager-Supervisor/1.0"})
                with urllib.request.urlopen(req, timeout=2.0) as resp:
                    status = getattr(resp, "status", None) or getattr(resp, "code", 200)
                    if status == 200:
                        logger.info("xray-checker is ready and accepting requests")
                        return True
            except Exception:
                time.sleep(poll_interval)

        raise TimeoutError(f"xray-checker failed to respond at {url} within {timeout_sec:.1f} seconds")

    def wait_for_first_check(self, timeout_sec: float = 30.0, poll_interval: float = 1.0) -> bool:
        """Waits until xray-checker has executed its initial proxy check iteration."""
        deadline = time.monotonic() + timeout_sec
        url = f"{self.base_url}/api/v1/proxies"
        logger.info("Waiting for initial proxy checks to finish (timeout: %.1fs)...", timeout_sec)

        while time.monotonic() < deadline:
            if self.process is not None and self.process.poll() is not None:
                return False

            try:
                import json
                req = urllib.request.Request(url, headers={"User-Agent": "DinoVoyager-Supervisor/1.0"})
                with urllib.request.urlopen(req, timeout=2.0) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    proxies = data.get("data", []) if isinstance(data, dict) else data
                    if isinstance(proxies, list) and proxies:
                        checked = [p for p in proxies if int(p.get("lastCheck", 0) or 0) > 0]
                        if len(checked) > 0:
                            logger.info("Initial proxy checks finished (%d/%d checked)", len(checked), len(proxies))
                            return True
            except Exception:
                pass
            time.sleep(poll_interval)

        logger.warning("Initial proxy checks did not complete within %.1fs, continuing with current state", timeout_sec)
        return False

    def is_healthy(self) -> bool:
        """Returns True if the child process is alive and /api/v1/status returns 200."""
        with self._lock:
            if self.process is None or self.process.poll() is not None:
                return False

        try:
            req = urllib.request.Request(
                f"{self.base_url}/api/v1/status",
                headers={"User-Agent": "DinoVoyager-Supervisor/1.0"},
            )
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                status = getattr(resp, "status", None) or getattr(resp, "code", 200)
                return status == 200
        except Exception:
            return False

    def restart_if_needed(self) -> bool:
        """Restarts the process if it crashed unexpectedly."""
        if self._should_stop:
            return False

        with self._lock:
            needs_restart = (self.process is None or self.process.poll() is not None)

        if needs_restart:
            exit_code = self.process.poll() if self.process else None
            logger.warning(
                "xray-checker is not running (exit code: %s). Auto-restarting...",
                exit_code,
            )
            self.stop()
            self.start()
            self.wait_until_ready()
            return True
        return False

    def stop(self, timeout_sec: float = 5.0) -> None:
        """Gracefully terminates the child process with SIGTERM, escalating to SIGKILL on timeout."""
        self._stop_sub_filter_server()
        with self._lock:
            self._should_stop = True
            proc = self.process
            self.process = None

        if proc is None:
            return

        pid = proc.pid
        if proc.poll() is not None:
            logger.debug("xray-checker (PID %d) already exited with code %d", pid, proc.poll())
            return

        logger.info("Stopping xray-checker (PID %d)...", pid)
        try:
            proc.terminate()
            try:
                proc.wait(timeout=timeout_sec)
                logger.info("xray-checker (PID %d) terminated cleanly", pid)
            except subprocess.TimeoutExpired:
                logger.warning(
                    "xray-checker (PID %d) did not terminate in %.1fs. Sending SIGKILL...",
                    pid,
                    timeout_sec,
                )
                proc.kill()
                proc.wait(timeout=2.0)
                logger.info("xray-checker (PID %d) killed", pid)
        except ProcessLookupError:
            pass
        except Exception as exc:
            logger.error("Error stopping xray-checker (PID %d): %s", pid, exc)
