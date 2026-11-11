"""Proxy tester and statistics aggregator communicating with local xray-checker daemon."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import logging
import re
import socket
import ssl
import time
from typing import Any, Dict, List, Optional, Tuple
import urllib.error
import urllib.request

from checker.config import CHECKER_VERSION

logger = logging.getLogger("dinovoyager-checker.tester")


class TesterError(Exception):
    """Raised on proxy query or parsing error."""
    pass


@dataclass
class RouteResult:
    """Individual tested proxy route."""

    name: str
    server: str
    port: int
    protocol: str
    location_code: str
    online: bool
    latency_ms: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TestSummary:
    """Aggregated connectivity and latency statistics."""

    __test__ = False

    total_routes: int = 0
    online_routes: int = 0
    avg_latency_ms: float = 0.0
    min_latency_ms: float = 0.0
    max_latency_ms: float = 0.0
    reliability_pct: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ── Dynamic location maps fetched from DinoVoyager API ──────────────────────
# These are populated/updated dynamically at runtime via fetch_location_maps() on every cycle.
_dynamic_ip_map: Dict[str, str] = {}
_dynamic_host_map: Dict[str, str] = {}

# Exported for backward compatibility with external imports / test suites
KNOWN_IP_MAP: Dict[str, str] = _dynamic_ip_map
KNOWN_HOST_MAP: Dict[str, str] = _dynamic_host_map

_location_maps_fetched_at: float = 0.0
_LOCATION_MAPS_TTL: float = 30.0  # Allow refreshing every polling cycle


def fetch_location_maps(
    api_url: str,
    api_key: str,
    timeout: float = 10.0,
    force: bool = False,
) -> None:
    """Fetch IP→code and host→code maps from the central DinoVoyager API.

    Calls GET {api_url}/api/info/locations with the checker API key and
    updates module-level ``_dynamic_ip_map`` / ``_dynamic_host_map`` in-place.
    Safe to call every polling cycle; updates existing maps dynamically.

    Args:
        api_url:  DinoVoyager API base URL (e.g. ``https://app.dinovoyager.net``).
        api_key:  Checker authentication key (sent as ``X-API-Key`` header).
        timeout:  HTTP request timeout in seconds.
        force:    Bypass cache TTL and force immediate refresh.
    """
    global _dynamic_ip_map, _dynamic_host_map, _location_maps_fetched_at

    if not api_url or not api_key:
        return

    now = time.monotonic()
    if not force and _location_maps_fetched_at > 0 and (now - _location_maps_fetched_at) < _LOCATION_MAPS_TTL:
        return  # Within throttle window

    base = api_url.rstrip("/")
    url = f"{base}/info/locations" if base.endswith("/api") else f"{base}/api/info/locations"
    req = urllib.request.Request(
        url,
        headers={
            "X-API-Key": str(api_key),
            "User-Agent": f"DinoVoyager-Checker/{CHECKER_VERSION}",
        },
    )

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            body = resp.read().decode("utf-8")
            data = json.loads(body)
    except urllib.error.HTTPError as err:
        logger.warning("Failed to fetch location maps from %s: HTTP %d %s", url, err.code, err.reason)
        return
    except urllib.error.URLError as err:
        logger.warning("Failed to fetch location maps from %s: %s", url, err)
        return
    except (socket.timeout, TimeoutError) as err:
        logger.warning("Timeout fetching location maps from %s: %s", url, err)
        return
    except json.JSONDecodeError as err:
        logger.warning("Malformed JSON in location maps response from %s: %s", url, err)
        return
    except Exception as err:
        logger.warning("Unexpected error fetching location maps from %s: %s", url, err)
        return

    locations = data.get("locations", []) if isinstance(data, dict) else []
    if not isinstance(locations, list):
        logger.warning("Unexpected locations payload shape from %s", url)
        return

    new_ip_map: Dict[str, str] = {}
    new_host_map: Dict[str, str] = {}

    for loc in locations:
        if not isinstance(loc, dict):
            continue
        code = str(loc.get("id") or loc.get("country_code") or "").strip().upper()
        if not code or len(code) > 4:
            continue

        for field in ("node_domain", "node_host", "host"):
            host_val = str(loc.get(field) or "").strip().lower()
            if host_val:
                new_host_map[host_val] = code

        ip_val = str(loc.get("node_ip") or "").strip()
        if ip_val:
            new_ip_map[ip_val] = code

    if new_host_map or new_ip_map:
        _dynamic_ip_map.update(new_ip_map)
        _dynamic_host_map.update(new_host_map)
        _location_maps_fetched_at = time.monotonic()
        logger.debug(
            "Location maps updated dynamically from API: %d IP entries, %d host entries",
            len(_dynamic_ip_map),
            len(_dynamic_host_map),
        )


def map_location_code(
    name: str = "",
    server: str = "",
    country_code: str = "",
    country: str = "",
) -> str:
    """Map route identifiers, names, hostnames, and IP addresses to standard ISO location codes."""
    # 1. Match dynamically fetched IPs (from DinoVoyager API, refreshed each cycle)
    clean_server = server.strip()
    if clean_server in _dynamic_ip_map:
        return _dynamic_ip_map[clean_server]

    # 2. Match dynamically fetched hostnames (from DinoVoyager API, refreshed each cycle)
    server_lower = clean_server.lower()
    for host_pattern, code in _dynamic_host_map.items():
        if host_pattern in server_lower:
            return code

    # 3. Match server subdomains (e.g. ee., ne., nl., fl., fi., de.)
    if server_lower.startswith("ee."):
        return "EE"
    if server_lower.startswith("ne.") or server_lower.startswith("nl."):
        return "NL"
    if server_lower.startswith("fl.") or server_lower.startswith("fi."):
        return "FI"
    if server_lower.startswith("de."):
        return "DE"

    # Also match dynamically known subdomains by prefix
    dot_first = server_lower.split(".")[0] if "." in server_lower else ""
    if dot_first:
        for host_pattern, code in _dynamic_host_map.items():
            prefix = host_pattern.split(".")[0] if "." in host_pattern else ""
            if prefix and dot_first == prefix:
                return code

    # 4. Match names and country names
    name_clean = name.strip()
    name_lower = name_clean.lower()
    country_lower = country.lower()

    # Check Estonia
    if (
        name_clean.startswith("EE-") or name_clean.startswith("EE_") or
        "эстония" in name_lower or "estonia" in name_lower or
        "эстония" in country_lower or "estonia" in country_lower
    ):
        return "EE"

    # Check Netherlands
    if (
        name_clean.startswith("NL-") or name_clean.startswith("NL_") or
        "нидерланды" in name_lower or "netherlands" in name_lower or
        "нидерланды" in country_lower or "netherlands" in country_lower or
        "play2go" in name_lower
    ):
        return "NL"

    # Check Finland
    if (
        name_clean.startswith("FI-") or name_clean.startswith("FI_") or
        "финляндия" in name_lower or "finland" in name_lower or
        "финляндия" in country_lower or "finland" in country_lower
    ):
        return "FI"

    # Check Germany
    if (
        name_clean.startswith("DE-") or name_clean.startswith("DE_") or
        "германия" in name_lower or "germany" in name_lower or
        "германия" in country_lower or "germany" in country_lower
    ):
        return "DE"

    # 5. Check explicitly provided ISO country code
    if country_code and len(country_code.strip()) == 2:
        return country_code.strip().upper()

    # 6. Check prefix in route name (e.g. "JP-Trojan", "US_VLESS")
    prefix_match = re.match(r"^([A-Za-z]{2})[-_ ]", name_clean)
    if prefix_match:
        return prefix_match.group(1).upper()

    return "UNKNOWN"


# Alias for backward compatibility
deduce_location_code = map_location_code


def compute_summary(routes: List[RouteResult]) -> TestSummary:
    """Compute aggregated summary statistics across tested routes."""
    total_routes = len(routes)
    online_routes = sum(1 for r in routes if r.online)

    if total_routes == 0:
        return TestSummary(
            total_routes=0,
            online_routes=0,
            avg_latency_ms=0.0,
            min_latency_ms=0.0,
            max_latency_ms=0.0,
            reliability_pct=0.0,
        )

    reliability_pct = round((online_routes / total_routes) * 100.0, 1)

    # Exclude offline routes or non-positive latencies from average calculation
    valid_latencies = [
        float(r.latency_ms)
        for r in routes
        if r.online and r.latency_ms is not None and r.latency_ms > 0
    ]

    if valid_latencies:
        avg_latency_ms = round(sum(valid_latencies) / len(valid_latencies), 1)
        min_latency_ms = round(min(valid_latencies), 1)
        max_latency_ms = round(max(valid_latencies), 1)
    else:
        avg_latency_ms = 0.0
        min_latency_ms = 0.0
        max_latency_ms = 0.0

    return TestSummary(
        total_routes=total_routes,
        online_routes=online_routes,
        avg_latency_ms=avg_latency_ms,
        min_latency_ms=min_latency_ms,
        max_latency_ms=max_latency_ms,
        reliability_pct=reliability_pct,
    )


# Alias for dictionary format
def compute_metrics(routes: List[Dict[str, Any]]) -> Dict[str, Any]:
    route_objs = [
        RouteResult(
            name=r.get("name", ""),
            server=r.get("server", ""),
            port=r.get("port", 443),
            protocol=r.get("protocol", "vless"),
            location_code=r.get("location_code", "UNKNOWN"),
            online=r.get("online", False),
            latency_ms=r.get("latency_ms"),
        )
        for r in routes
    ]
    summary = compute_summary(route_objs)
    return summary.to_dict()


def poll_proxies(
    host: str = "127.0.0.1",
    port: int = 2112,
    timeout: float = 10.0,
    excluded_routes: Optional[Sequence[str]] = None,
) -> Tuple[List[RouteResult], TestSummary]:
    """Poll local xray-checker daemon REST API and parse results."""
    url = f"http://{host}:{port}/api/v1/proxies"
    req = urllib.request.Request(url, headers={"User-Agent": f"DinoVoyager-Checker/{CHECKER_VERSION}"})

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            data = json.loads(body)
    except urllib.error.URLError as err:
        logger.warning("Failed to connect to local xray-checker at %s: %s", url, err)
        return [], TestSummary()
    except (socket.timeout, TimeoutError) as err:
        logger.warning("Timeout while polling xray-checker at %s: %s", url, err)
        return [], TestSummary()
    except json.JSONDecodeError as err:
        logger.warning("Received malformed JSON from xray-checker at %s: %s", url, err)
        return [], TestSummary()
    except Exception as err:
        logger.warning("Unexpected error polling xray-checker at %s: %s", url, err)
        return [], TestSummary()

    # Extract proxy items
    items = []
    if isinstance(data, dict):
        items = data.get("data", [])
        if not isinstance(items, list):
            items = []
    elif isinstance(data, list):
        items = data

    routes: List[RouteResult] = []
    excluded_set = {str(e).strip().lower() for e in (excluded_routes or []) if str(e).strip()}

    for item in items:
        if not isinstance(item, dict):
            continue

        name = item.get("name", "Unnamed")
        server = item.get("server", "unknown")

        try:
            port_val = int(item.get("port", 443))
        except (ValueError, TypeError):
            port_val = 443

        # Skip virtual balancer routes or routes with port 1234
        if port_val == 1234 or ":1234" in str(server) or ":1234" in str(name):
            continue

        # Skip or ignore route if its name or server is excluded
        name_lower = str(name).strip().lower()
        server_lower = str(server).strip().lower()
        if any(ex in name_lower or ex in server_lower for ex in excluded_set):
            continue
        protocol = str(item.get("protocol", "unknown"))
        online = bool(item.get("online", False))

        raw_lat = item.get("latencyMs")
        if raw_lat is None:
            raw_lat = item.get("latency_ms")

        latency_ms: Optional[float] = None
        if raw_lat is not None and online:
            try:
                val = float(raw_lat)
                if val >= 0:
                    latency_ms = val
            except (ValueError, TypeError):
                latency_ms = None

        country_code = item.get("countryCode") or item.get("country_code") or ""
        country = item.get("country", "")

        loc_code = map_location_code(
            name=name,
            server=server,
            country_code=country_code,
            country=country,
        )

        routes.append(
            RouteResult(
                name=name,
                server=server,
                port=port_val,
                protocol=protocol,
                location_code=loc_code,
                online=online,
                latency_ms=latency_ms,
            )
        )

    summary = compute_summary(routes)
    return routes, summary


class ProxyTester:
    """Wrapper class providing object-oriented access to proxy testing."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 2112,
        timeout_sec: float = 10.0,
        xray_url: Optional[str] = None,
        base_url: Optional[str] = None,
        api_url: Optional[str] = None,
        api_key: Optional[str] = None,
        **kwargs: Any,
    ):
        target = xray_url or base_url
        if target:
            from urllib.parse import urlparse
            u = urlparse(target)
            self.host = u.hostname or host
            self.port = u.port or (443 if u.scheme == "https" else 80 if u.scheme == "http" else port)
        else:
            self.host = host
            self.port = port
        self.timeout = timeout_sec
        self.api_url = api_url
        self.api_key = api_key
        self.excluded_routes: List[str] = []

    def set_excluded_routes(self, routes: List[str]) -> None:
        self.excluded_routes = list(routes) if routes else []

    def fetch_and_compute_metrics(self) -> Dict[str, Any]:
        # Dynamically refresh location maps from DinoVoyager API before evaluating routes
        if self.api_url and self.api_key:
            try:
                fetch_location_maps(api_url=self.api_url, api_key=self.api_key, timeout=self.timeout)
            except Exception as exc:
                logger.warning("Error refreshing location maps from API: %s", exc)

        routes, summary = poll_proxies(
            host=self.host,
            port=self.port,
            timeout=self.timeout,
            excluded_routes=self.excluded_routes,
        )
        return {
            "routes": [r.to_dict() for r in routes],
            "summary": summary.to_dict(),
        }

    def run_check(self) -> Dict[str, Any]:
        return self.fetch_and_compute_metrics()
