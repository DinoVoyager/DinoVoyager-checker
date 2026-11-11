"""Residential ISP and Geolocation detection with multi-provider fallback and caching."""

from __future__ import annotations

import json
import logging
import re
import socket
import ssl
import time
from typing import Any, Dict, Optional
import urllib.error
import urllib.request

from checker.config import CHECKER_VERSION

logger = logging.getLogger("dinovoyager-checker.geo")

GEO_CACHE_TTL = 4 * 3600  # 4 hours in seconds


class GeoInfo(dict):
    """Dictionary holding detected geolocation and network origin data with attribute access."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.__dict__ = self

    # Attribute defaults
    ip: str = "0.0.0.0"
    isp: str = "Unknown"
    org: str = "Unknown"
    asn: str = "Unknown"
    country: str = "Unknown"
    country_code: str = "UNKNOWN"
    city: str = "Unknown"
    latitude: float = 0.0
    longitude: float = 0.0
    region: str = ""
    timezone: str = ""
    name: str = ""


# In-memory global cache
_cached_geo: Optional[GeoInfo] = None
_cache_timestamp: float = 0.0


def clear_cache() -> None:
    """Clear in-memory geo cache (used for testing and cache reset)."""
    global _cached_geo, _cache_timestamp
    _cached_geo = None
    _cache_timestamp = 0.0


def normalize_asn(raw: Optional[str]) -> str:
    """Standardize ASN into 'AS<number>' format."""
    if not raw:
        return "Unknown"
    match = re.search(r"(?:AS)?(\d+)", str(raw), re.IGNORECASE)
    if match:
        return f"AS{match.group(1)}"
    return "Unknown"


def _read_response(resp_obj: Any) -> str:
    raw = resp_obj.read()
    if isinstance(raw, bytes):
        return raw.decode("utf-8")
    if isinstance(raw, str):
        return raw
    if hasattr(resp_obj, "__enter__"):
        enter_val = resp_obj.__enter__()
        raw_enter = enter_val.read()
        if isinstance(raw_enter, bytes):
            return raw_enter.decode("utf-8")
        if isinstance(raw_enter, str):
            return raw_enter
    return str(raw)


def _query_ip_api(ip: Optional[str] = None, timeout: float = 5.0) -> Optional[GeoInfo]:
    url = "http://ip-api.com/json/"
    if ip:
        url += f"{ip}"
    url += "?fields=status,message,country,countryCode,region,regionName,city,lat,lon,timezone,isp,org,as,query"

    req = urllib.request.Request(url, headers={"User-Agent": f"DinoVoyager-Checker/{CHECKER_VERSION}", "Accept": "application/json"})

    resp_obj = urllib.request.urlopen(req, timeout=timeout)
    try:
        body = _read_response(resp_obj)
        data = json.loads(body)
    finally:
        if hasattr(resp_obj, "close"):
            try:
                resp_obj.close()
            except Exception:
                pass

    if data.get("status") != "success":
        logger.warning("ip-api.com returned non-success: %s", data.get("message"))
        return None

    return GeoInfo(
        ip=str(data.get("query", "0.0.0.0")).strip(),
        isp=str(data.get("isp", "Unknown")).strip(),
        org=str(data.get("org") or data.get("isp") or "Unknown").strip(),
        asn=normalize_asn(data.get("as")),
        country=str(data.get("country", "Unknown")).strip(),
        country_code=str(data.get("countryCode") or "UNKNOWN").strip().upper(),
        city=str(data.get("city", "Unknown")).strip(),
        latitude=float(data.get("lat") or 0.0),
        longitude=float(data.get("lon") or 0.0),
        region=str(data.get("regionName", "")).strip(),
        timezone=str(data.get("timezone", "")).strip(),
    )


def _query_ipinfo(ip: Optional[str] = None, timeout: float = 5.0) -> Optional[GeoInfo]:
    url = f"https://ipinfo.io/{ip + '/' if ip else ''}json"
    req = urllib.request.Request(url, headers={"User-Agent": f"DinoVoyager-Checker/{CHECKER_VERSION}", "Accept": "application/json"})

    resp_obj = urllib.request.urlopen(req, timeout=timeout)
    try:
        body = _read_response(resp_obj)
        data = json.loads(body)
    finally:
        if hasattr(resp_obj, "close"):
            try:
                resp_obj.close()
            except Exception:
                pass

    lat, lon = 0.0, 0.0
    if "loc" in data and "," in str(data["loc"]):
        try:
            parts = str(data["loc"]).split(",")
            lat = float(parts[0].strip())
            lon = float(parts[1].strip())
        except (ValueError, IndexError):
            lat, lon = 0.0, 0.0

    org_str = str(data.get("org", "")).strip()
    asn = normalize_asn(org_str)
    isp = re.sub(r"^AS\d+\s*", "", org_str).strip() or org_str or "Unknown"
    cc = str(data.get("country") or "UNKNOWN").strip().upper()

    return GeoInfo(
        ip=str(data.get("ip", "0.0.0.0")).strip(),
        isp=isp,
        org=org_str or "Unknown",
        asn=asn,
        country=str(data.get("country", "Unknown")).strip(),
        country_code=cc,
        city=str(data.get("city", "Unknown")).strip(),
        latitude=lat,
        longitude=lon,
        region=str(data.get("region", "")).strip(),
        timezone=str(data.get("timezone", "")).strip(),
    )


def _query_ipwhois(ip: Optional[str] = None, timeout: float = 5.0) -> Optional[GeoInfo]:
    url = f"https://ipwhois.app/json/{ip if ip else ''}"
    req = urllib.request.Request(url, headers={"User-Agent": f"DinoVoyager-Checker/{CHECKER_VERSION}", "Accept": "application/json"})

    resp_obj = urllib.request.urlopen(req, timeout=timeout)
    try:
        body = _read_response(resp_obj)
        data = json.loads(body)
    finally:
        if hasattr(resp_obj, "close"):
            try:
                resp_obj.close()
            except Exception:
                pass

    if not data.get("success", True):
        logger.warning("ipwhois.app returned unsuccessful status")
        return None

    cc = str(data.get("country_code") or data.get("countryCode") or "UNKNOWN").strip().upper()
    return GeoInfo(
        ip=str(data.get("ip", "0.0.0.0")).strip(),
        isp=str(data.get("isp", "Unknown")).strip(),
        org=str(data.get("org") or data.get("isp") or "Unknown").strip(),
        asn=normalize_asn(data.get("asn")),
        country=str(data.get("country", "Unknown")).strip(),
        country_code=cc,
        city=str(data.get("city", "Unknown")).strip(),
        latitude=float(data.get("latitude") or 0.0),
        longitude=float(data.get("longitude") or 0.0),
        region=str(data.get("region", "")).strip(),
        timezone=str(data.get("timezone", "")).strip(),
    )


def detect_geo(
    checker_name: str = "",
    force_refresh: bool = False,
    cache_ttl: float = GEO_CACHE_TTL,
    timeout: float = 5.0,
    current_ip: Optional[str] = None,
    ip: Optional[str] = None,
    **kwargs: Any,
) -> GeoInfo:
    """Detect residential ISP and Geolocation with caching and multi-provider fallback."""
    global _cached_geo, _cache_timestamp

    effective_ip = ip or current_ip
    now = time.time()

    # 1. Check in-memory cache
    if _cached_geo is not None and not force_refresh:
        cached_ip = _cached_geo.get("ip", "0.0.0.0")
        if effective_ip and cached_ip not in ("0.0.0.0", "unknown") and cached_ip != effective_ip:
            logger.info("External IP changed from %s to %s, invalidating geo cache", cached_ip, effective_ip)
            _cached_geo = None
        elif (now - _cache_timestamp) < cache_ttl and (effective_ip is None or effective_ip == cached_ip):
            res = GeoInfo(**_cached_geo)
            if checker_name:
                res["name"] = checker_name
            return res

    # 2. Try primary: ip-api.com
    try:
        data = _query_ip_api(ip=effective_ip, timeout=timeout)
        if data:
            if checker_name:
                data["name"] = checker_name
            _cached_geo = data
            _cache_timestamp = now
            return data
    except Exception as exc:
        logger.warning("Primary geo provider (ip-api.com) failed: %s. Falling back...", exc)

    # 3. Try secondary: ipinfo.io
    try:
        data = _query_ipinfo(ip=effective_ip, timeout=timeout)
        if data:
            if checker_name:
                data["name"] = checker_name
            _cached_geo = data
            _cache_timestamp = now
            return data
    except Exception as exc:
        logger.warning("Secondary geo provider (ipinfo.io) failed: %s. Falling back...", exc)

    # 4. Try tertiary: ipwhois.app
    try:
        data = _query_ipwhois(ip=effective_ip, timeout=timeout)
        if data:
            if checker_name:
                data["name"] = checker_name
            _cached_geo = data
            _cache_timestamp = now
            return data
    except Exception as exc:
        logger.warning("Tertiary geo provider (ipwhois.app) failed: %s.", exc)

    # 5. All providers failed: safe graceful default
    logger.error("All IP and Geolocation probes failed. Operating in offline/fallback mode.")
    fallback = GeoInfo(
        ip=effective_ip or "0.0.0.0",
        isp="Unknown",
        org="Unknown",
        asn="Unknown",
        country="Unknown",
        country_code="UNKNOWN",
        city="Unknown",
        latitude=0.0,
        longitude=0.0,
        name=checker_name,
    )
    _cached_geo = fallback
    _cache_timestamp = now
    return fallback


class GeoDetector:
    """Wrapper class providing object-oriented access to geolocation detection."""

    def __init__(self, cache_ttl: float = GEO_CACHE_TTL):
        self.cache_ttl = cache_ttl

    def get_geo_info(
        self,
        checker_name: str = "",
        force_refresh: bool = False,
        current_ip: Optional[str] = None,
    ) -> GeoInfo:
        return detect_geo(
            checker_name=checker_name,
            force_refresh=force_refresh,
            cache_ttl=self.cache_ttl,
            current_ip=current_ip,
        )

    def clear_cache(self) -> None:
        clear_cache()
