"""Report assembly and HTTP transmission module for DinoVoyager Checker."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import socket
import sys
import time
from typing import Any, Dict, List, Optional, Sequence
import urllib.error
import urllib.request

logger = logging.getLogger("dinovoyager-checker.reporter")

CHECKER_VERSION = "1.0.0"


class AuthError(Exception):
    """Raised when central API returns 401 Unauthorized or 403 Forbidden."""
    pass


def mask_api_key(key: str) -> str:
    """Mask sensitive API key for safe logging."""
    if len(key) <= 8:
        return "***"
    return f"{key[:8]}...{key[-4:]}"


def assemble_payload(
    config: Any,
    geo: Any,
    routes: List[Any],
    summary: Any,
) -> Dict[str, Any]:
    """Construct JSON report payload matching PROJECT.md § Interface Contracts: 1."""
    now_ts = int(time.time())
    collected_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # API Key
    api_key = getattr(config, "api_key", None)
    if api_key is None and isinstance(config, dict):
        api_key = config.get("api_key", "")
    api_key = str(api_key or "")

    # Checker Name
    custom_name = getattr(config, "checker_name", None)
    if custom_name is None and isinstance(config, dict):
        custom_name = config.get("checker_name")

    # ISP and Hostname resolution
    geo_isp = getattr(geo, "isp", None) if not isinstance(geo, dict) else geo.get("isp")
    isp_clean = geo_isp if (geo_isp and geo_isp != "Unknown") else "ISP"

    if custom_name:
        checker_name = str(custom_name)
    else:
        try:
            hostname = socket.gethostname() or "checker"
        except Exception:
            hostname = "checker"
        checker_name = f"{hostname}-{isp_clean}"

    # Extract Geo fields
    def _g(field: str, default: Any) -> Any:
        if isinstance(geo, dict):
            return geo.get(field, default)
        return getattr(geo, field, default)

    checker_info = {
        "name": checker_name,
        "ip": str(_g("ip", "0.0.0.0")),
        "isp": str(_g("isp", "Unknown")),
        "org": str(_g("org", _g("isp", "Unknown"))),
        "asn": str(_g("asn", "Unknown")),
        "country": str(_g("country", "Unknown")),
        "country_code": str(_g("country_code", "UNKNOWN")).upper(),
        "city": str(_g("city", "Unknown")),
        "latitude": float(_g("latitude", 0.0)),
        "longitude": float(_g("longitude", 0.0)),
    }

    # Extract Routes
    formatted_routes = []
    for r in routes:
        if isinstance(r, dict):
            name = str(r.get("name", ""))
            server = str(r.get("server", ""))
            port = int(r.get("port") or 443)
            protocol = str(r.get("protocol", "vless"))
            location_code = str(r.get("location_code", "UNKNOWN"))
            online = bool(r.get("online", False))
            raw_lat = r.get("latency_ms")
        else:
            name = str(getattr(r, "name", ""))
            server = str(getattr(r, "server", ""))
            port = int(getattr(r, "port", 443))
            protocol = str(getattr(r, "protocol", "vless"))
            location_code = str(getattr(r, "location_code", "UNKNOWN"))
            online = bool(getattr(r, "online", False))
            raw_lat = getattr(r, "latency_ms", None)

        latency_ms = float(raw_lat) if (raw_lat is not None and online) else 0.0

        formatted_routes.append(
            {
                "name": name,
                "server": server,
                "port": port,
                "protocol": protocol,
                "location_code": location_code,
                "online": online,
                "latency_ms": latency_ms,
            }
        )

    # Extract Summary
    def _s(field: str, default: Any) -> Any:
        if isinstance(summary, dict):
            return summary.get(field, default)
        return getattr(summary, field, default)

    total_routes = int(_s("total_routes", len(formatted_routes)))
    online_routes = int(_s("online_routes", sum(1 for r in formatted_routes if r["online"])))
    avg_latency_ms = float(_s("avg_latency_ms", 0.0))
    reliability_pct = float(_s("reliability_pct", 0.0))

    formatted_summary = {
        "total_routes": total_routes,
        "online_routes": online_routes,
        "avg_latency_ms": avg_latency_ms,
        "reliability_pct": reliability_pct,
    }

    return {
        "api_key": api_key,
        "checker_version": CHECKER_VERSION,
        "timestamp": now_ts,
        "collected_at": collected_at,
        "checker_info": checker_info,
        "routes": formatted_routes,
        "summary": formatted_summary,
    }


# Alias for backward compatibility
build_report = assemble_payload


def send_report(
    payload: Dict[str, Any],
    config: Any,
    max_retries: int = 3,
    backoff_delays: Sequence[float] = (2.0, 5.0, 10.0),
    timeout: float = 15.0,
) -> bool:
    """Send connectivity report to central DinoVoyager API.

    Handles authentication errors (401/403) without rapid retry,
    falls back to secondary URL endpoint on 404, and performs
    exponential backoff retry on 5xx and transient network errors.
    """
    payload_json = json.dumps(payload)
    payload_bytes = payload_json.encode("utf-8")

    api_url = getattr(config, "api_url", None)
    if api_url is None and isinstance(config, dict):
        api_url = config.get("api_url", "")
    api_url = str(api_url or "").rstrip("/")

    api_key = payload.get("api_key") or getattr(config, "api_key", "")
    if isinstance(config, dict) and not api_key:
        api_key = config.get("api_key", "")

    headers = {
        "Content-Type": "application/json",
        "X-API-Key": str(api_key),
        "User-Agent": f"DinoVoyager-Checker/{CHECKER_VERSION}",
    }

    primary_url = f"{api_url}/api/checkers/report"
    fallback_url = f"{api_url}/checkers/report"
    current_url = primary_url

    for attempt in range(max_retries):
        req = urllib.request.Request(
            current_url,
            data=payload_bytes,
            headers=headers,
            method="POST",
        )

        try:
            raw_resp = urllib.request.urlopen(req, timeout=timeout)
            status = getattr(raw_resp, "status", None)
            if status is None or not isinstance(status, int):
                status = getattr(raw_resp, "code", None)
            if status is None or not isinstance(status, int):
                if hasattr(raw_resp, "__enter__"):
                    resp_val = raw_resp.__enter__()
                    status = getattr(resp_val, "status", None) or getattr(resp_val, "code", None)
            if not isinstance(status, int):
                status = 200

            if status in (200, 201, 202):
                logger.info("Report delivered successfully to %s: HTTP %d", current_url, status)
                return True
            elif status >= 500:
                logger.warning("Server returned HTTP %d on attempt %d/%d", status, attempt + 1, max_retries)

        except urllib.error.HTTPError as http_err:
            status = http_err.code

            # Handle 401 Unauthorized / 403 Forbidden: permanent client error, no rapid retry
            if status in (401, 403):
                masked = mask_api_key(str(api_key))
                logger.error("API Key '%s' rejected by server (HTTP %d): %s", masked, status, http_err.reason)
                return False

            # Handle 404: try fallback endpoint if on primary
            if status == 404 and current_url == primary_url:
                logger.info("Primary endpoint %s returned 404, attempting fallback %s", primary_url, fallback_url)
                current_url = fallback_url
                req_fb = urllib.request.Request(current_url, data=payload_bytes, headers=headers, method="POST")
                try:
                    raw_fb = urllib.request.urlopen(req_fb, timeout=timeout)
                    fb_status = getattr(raw_fb, "status", None)
                    if fb_status is None or not isinstance(fb_status, int):
                        fb_status = getattr(raw_fb, "code", None)
                    if fb_status is None or not isinstance(fb_status, int):
                        if hasattr(raw_fb, "__enter__"):
                            fb_enter = raw_fb.__enter__()
                            fb_status = getattr(fb_enter, "status", None) or getattr(fb_enter, "code", None)
                    if not isinstance(fb_status, int):
                        fb_status = 200

                    if fb_status in (200, 201, 202):
                        logger.info("Report delivered successfully to fallback %s: HTTP %d", current_url, fb_status)
                        return True
                except urllib.error.HTTPError as fb_err:
                    if fb_err.code in (401, 403):
                        logger.error("API Key rejected on fallback (HTTP %d)", fb_err.code)
                        return False
                    logger.warning("Fallback endpoint returned HTTP %d: %s", fb_err.code, fb_err.reason)
                except Exception as fb_err:
                    logger.warning("Fallback endpoint error: %s", fb_err)

            # Handle 5xx errors: retry with backoff
            if 500 <= status < 600:
                logger.warning("Server error (HTTP %d) on attempt %d/%d: %s", status, attempt + 1, max_retries, http_err.reason)
            else:
                logger.warning("HTTP error %d on attempt %d/%d", status, attempt + 1, max_retries)

        except (socket.timeout, TimeoutError) as timeout_err:
            logger.warning("Timeout connecting to %s on attempt %d/%d: %s", current_url, attempt + 1, max_retries, timeout_err)
        except urllib.error.URLError as url_err:
            logger.warning("Network connection error to %s on attempt %d/%d: %s", current_url, attempt + 1, max_retries, url_err)
        except Exception as exc:
            logger.warning("Unexpected error during report delivery on attempt %d/%d: %s", attempt + 1, max_retries, exc)

        # Apply exponential backoff delay before next attempt
        if attempt < max_retries - 1:
            delay = backoff_delays[attempt] if attempt < len(backoff_delays) else backoff_delays[-1]
            logger.info("Retrying in %.1f seconds...", delay)
            time.sleep(delay)

    logger.error("All %d delivery attempts failed. Will try again next cycle.", max_retries)
    return False


class ReportManager:
    """Wrapper class providing object-oriented access to report assembly and delivery."""

    def __init__(self, config: Any, max_retries: int = 3, base_backoff: float = 2.0):
        self.config = config
        self.max_retries = max_retries
        self.base_backoff = base_backoff

    def build_payload(self, geo_data: Any, routes_data: Any) -> Dict[str, Any]:
        routes = routes_data.get("routes", []) if isinstance(routes_data, dict) else routes_data
        summary = routes_data.get("summary", {}) if isinstance(routes_data, dict) else {}
        return assemble_payload(self.config, geo_data, routes, summary)

    def send_report(self, payload: Dict[str, Any]) -> bool:
        return send_report(payload, self.config, max_retries=self.max_retries)
