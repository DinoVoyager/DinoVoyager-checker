"""Configuration loader and validator for DinoVoyager Checker.

Handles parameter ingestion from CLI arguments and environment variables with
strict CLI precedence, validation, and descriptive error formatting.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
import socket
import sys
from typing import Dict, List, Optional
from urllib.parse import urlparse


class ConfigError(Exception):
    """Raised when configuration parsing or validation fails."""
    pass


class _CustomArgumentParser(argparse.ArgumentParser):
    """ArgumentParser that raises ConfigError instead of exiting directly on parse errors."""

    def error(self, message: str) -> None:
        raise ConfigError(f"Argument parsing error: {message}")


CHECKER_VERSION = "1.0.3"
VERSION = CHECKER_VERSION


@dataclass
class Config:
    """Checker runtime configuration."""

    subscription_url: str
    polling_interval: int
    api_url: str
    api_key: str
    checker_name: Optional[str] = None
    proxy_timeout: int = 10
    log_level: str = "INFO"
    region: str = "RU-MOW"
    version: str = CHECKER_VERSION


# Alias for backward/test compatibility
CheckerConfig = Config


def _build_parser() -> _CustomArgumentParser:
    parser = _CustomArgumentParser(
        prog="dinovoyager-checker",
        description="Lightweight distributed connectivity checker for DinoVoyager.",
        add_help=False,
    )
    parser.add_argument(
        "-s", "--subscription-url",
        dest="subscription_url",
        default=None,
        help="Subscription URL delivering proxy routes (ENV: SUBSCRIPTION_URL).",
    )
    parser.add_argument(
        "-i", "--interval",
        dest="polling_interval",
        default=None,
        help="Polling interval in seconds, minimum 15 (default: 300, ENV: POLLING_INTERVAL).",
    )
    parser.add_argument(
        "-u", "--api-url",
        dest="api_url",
        default=None,
        help="DinoVoyager central API URL (ENV: API_URL).",
    )
    parser.add_argument(
        "-k", "--api-key",
        dest="api_key",
        default=None,
        help="Authentication API key generated in Admin Panel (ENV: API_KEY).",
    )
    parser.add_argument(
        "-n", "--name",
        dest="checker_name",
        default=None,
        help="Optional human-readable checker identifier (ENV: CHECKER_NAME).",
    )
    parser.add_argument(
        "-r", "--region",
        dest="region",
        default=None,
        help="Assigned Russian region code, e.g. RU-MOW (default: RU-MOW, ENV: REGION_CODE / REGION).",
    )
    parser.add_argument(
        "--timeout",
        dest="proxy_timeout",
        default=None,
        help="Timeout in seconds for proxy testing (default: 10, ENV: PROXY_TIMEOUT).",
    )
    parser.add_argument(
        "--log-level",
        dest="log_level",
        default=None,
        help="Logging verbosity: debug, info, warn/warning, error (default: info, ENV: LOG_LEVEL).",
    )
    parser.add_argument(
        "-h", "--help",
        action="store_true",
        dest="show_help",
        help="Show this help message and exit.",
    )
    return parser


def load_config(
    args: Optional[List[str]] = None,
    env: Optional[Dict[str, str]] = None,
    argv: Optional[List[str]] = None,
    environ: Optional[Dict[str, str]] = None,
    exit_on_error: bool = False,
) -> Config:
    """Load configuration with CLI precedence over Environment Variables.

    Args:
        args / argv: CLI arguments (defaults to sys.argv[1:]).
        env / environ: Environment variable dictionary (defaults to os.environ).
        exit_on_error: If True, prints user error to stderr and calls sys.exit(1).
                       If False, raises ConfigError.

    Returns:
        Validated Config dataclass instance.

    Raises:
        ConfigError: If any required parameter is missing or validation fails.
    """
    raw_args = argv if argv is not None else args
    if raw_args is None:
        raw_args = sys.argv[1:]

    raw_env = environ if environ is not None else env
    if raw_env is None:
        raw_env = os.environ

    parser = _build_parser()
    try:
        parsed_args, _ = parser.parse_known_args(raw_args)
    except ConfigError as err:
        if exit_on_error:
            print(f"Error: {err}", file=sys.stderr)
            sys.exit(1)
        raise

    if getattr(parsed_args, "show_help", False):
        parser.print_help()
        sys.exit(0)

    errors: List[str] = []

    # 1. SUBSCRIPTION_URL (Required)
    sub_url = (parsed_args.subscription_url or raw_env.get("SUBSCRIPTION_URL", "")).strip()
    if not sub_url:
        errors.append("SUBSCRIPTION_URL is required (CLI: --subscription-url / -s, ENV: SUBSCRIPTION_URL)")
    else:
        parsed_sub = urlparse(sub_url)
        if parsed_sub.scheme not in ("http", "https"):
            errors.append(f"Invalid SUBSCRIPTION_URL scheme: must start with 'http://' or 'https://' (got '{sub_url}')")

    # 2. POLLING_INTERVAL (Required, minimum 15)
    interval_raw = parsed_args.polling_interval or raw_env.get("POLLING_INTERVAL")
    polling_interval = 0
    if interval_raw is None or str(interval_raw).strip() == "":
        errors.append("POLLING_INTERVAL is required (CLI: --interval / -i, ENV: POLLING_INTERVAL)")
    else:
        try:
            polling_interval = int(interval_raw)
            if polling_interval < 15:
                errors.append(f"POLLING_INTERVAL must be at least 15 seconds (got {polling_interval})")
        except (ValueError, TypeError):
            errors.append(f"Invalid integer literal for POLLING_INTERVAL: '{interval_raw}'")

    # 3. API_URL (Required)
    api_url = (parsed_args.api_url or raw_env.get("API_URL", "")).strip()
    if not api_url:
        errors.append("API_URL is required (CLI: --api-url / -u, ENV: API_URL)")
    else:
        api_url = api_url.rstrip("/")
        parsed_api = urlparse(api_url)
        if parsed_api.scheme not in ("http", "https"):
            errors.append(f"Invalid API_URL scheme: must start with 'http://' or 'https://' (got '{api_url}')")

    # 4. API_KEY (Required)
    api_key = (parsed_args.api_key or raw_env.get("API_KEY", "")).strip()
    if not api_key:
        errors.append("API_KEY is required (CLI: --api-key / -k, ENV: API_KEY)")

    # Optional: CHECKER_NAME (default: None)
    checker_name = parsed_args.checker_name or raw_env.get("CHECKER_NAME") or None
    if checker_name is not None:
        checker_name = checker_name.strip() or None

    # Optional: PROXY_TIMEOUT (default: 10)
    timeout_raw = parsed_args.proxy_timeout or raw_env.get("PROXY_TIMEOUT") or "10"
    proxy_timeout = 10
    try:
        proxy_timeout = int(timeout_raw)
        if proxy_timeout < 1:
            errors.append(f"PROXY_TIMEOUT must be at least 1 second (got {proxy_timeout})")
    except (ValueError, TypeError):
        errors.append(f"Invalid integer literal for PROXY_TIMEOUT: '{timeout_raw}'")

    # Optional: LOG_LEVEL (default: INFO)
    log_level = (parsed_args.log_level or raw_env.get("LOG_LEVEL") or "INFO").strip().upper()
    if log_level == "WARN":
        log_level = "WARNING"
    if log_level not in ("DEBUG", "INFO", "WARNING", "ERROR"):
        errors.append(f"Invalid LOG_LEVEL: must be DEBUG, INFO, WARN/WARNING, or ERROR (got '{log_level}')")

    # Optional: REGION (default: RU-MOW)
    raw_region = parsed_args.region or raw_env.get("REGION_CODE") or raw_env.get("REGION") or "RU-MOW"
    region = str(raw_region).strip().upper() or "RU-MOW"

    if errors:
        msg = "Configuration Error:\n" + "\n".join(f"  - {err}" for err in errors)
        if exit_on_error:
            print(msg, file=sys.stderr)
            print("\nRun with --help for command-line syntax and options.", file=sys.stderr)
            sys.exit(1)
        raise ConfigError(msg)

    return Config(
        subscription_url=sub_url,
        polling_interval=polling_interval,
        api_url=api_url,
        api_key=api_key,
        checker_name=checker_name,
        proxy_timeout=proxy_timeout,
        log_level=log_level,
        region=region,
    )
