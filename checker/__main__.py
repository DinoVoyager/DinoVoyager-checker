"""CLI entrypoint for DinoVoyager Distributed Checker.

Orchestrates parameter loading, Geo/ISP origin detection, process supervision
for embedded xray-checker, periodic connectivity evaluation, and report dispatch.
"""

from __future__ import annotations

import logging
import signal
import sys
import threading
import time
from typing import Optional

from checker import __version__
from checker.config import load_config
from checker.geo import GeoDetector
from checker.reporter import ReportManager
from checker.supervisor import SupervisorError, XraySupervisor
from checker.tester import ProxyTester, fetch_location_maps


def setup_logging(log_level: str) -> None:
    """Configures structured console logging."""
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def main(argv: Optional[list[str]] = None) -> int:
    """Main execution function."""
    config = load_config(argv=argv, exit_on_error=True)
    setup_logging(config.log_level)
    logger = logging.getLogger("dinovoyager-checker")

    logger.info("Starting DinoVoyager Checker v%s (Name: '%s')", __version__, config.checker_name)
    logger.info("Subscription URL: %s", config.subscription_url)
    logger.info("Reporting API URL: %s", config.api_url)
    logger.info("Polling Interval: %d seconds (Timeout: %ds)", config.polling_interval, config.proxy_timeout)

    # Shutdown event for clean signal interruption
    shutdown_event = threading.Event()

    def signal_handler(signum, frame):
        sig_name = signal.Signals(signum).name
        logger.info("Received termination signal %s. Initiating graceful shutdown...", sig_name)
        shutdown_event.set()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Initialize subsystems
    geo_detector = GeoDetector()
    supervisor = XraySupervisor(
        subscription_url=config.subscription_url,
        polling_interval=config.polling_interval,
        proxy_timeout=config.proxy_timeout,
        log_level=config.log_level,
    )
    tester = ProxyTester(
        xray_url=supervisor.base_url,
        timeout_sec=float(config.proxy_timeout),
        api_url=config.api_url,
        api_key=config.api_key,
    )
    reporter = ReportManager(config=config)

    # Initial Location maps resolution from DinoVoyager central API
    logger.info("Fetching node location mappings from central API (%s)...", config.api_url)
    try:
        fetch_location_maps(api_url=config.api_url, api_key=config.api_key, timeout=float(config.proxy_timeout))
    except Exception as exc:
        logger.warning("Could not pre-fetch location maps from central API: %s", exc)

    # Initial Geo resolution
    logger.info("Resolving origin network geolocation and residential ISP...")
    initial_geo = geo_detector.get_geo_info(checker_name=config.checker_name)
    logger.info(
        "Origin: %s, %s (ISP: %s, ASN: %s, IP: %s)",
        initial_geo.get("city"),
        initial_geo.get("country_code"),
        initial_geo.get("isp"),
        initial_geo.get("asn"),
        initial_geo.get("ip"),
    )

    # Launch supervisor
    try:
        logger.info("Launching embedded xray-checker engine...")
        supervisor.start()
        supervisor.wait_until_ready(timeout_sec=30.0)
    except Exception as exc:
        logger.error("Failed to initialize xray-checker supervisor: %s", exc)
        supervisor.stop()
        return 1

    logger.info("xray-checker engine online. Entering continuous monitoring loop...")

    try:
        while not shutdown_event.is_set():
            loop_start = time.monotonic()

            # 1. Healthcheck and auto-recovery
            try:
                supervisor.restart_if_needed()
            except Exception as exc:
                logger.error("Error during supervisor healthcheck: %s", exc)

            # 2. Update Geo & ISP (uses 4-hour in-memory cache)
            try:
                geo_info = geo_detector.get_geo_info(checker_name=config.checker_name)
            except Exception as exc:
                logger.warning("Error resolving geo info: %s", exc)
                geo_info = initial_geo

            # 3. Query route metrics
            logger.info("Evaluating proxy routes connectivity...")
            routes_data = tester.fetch_and_compute_metrics()
            summary = routes_data.get("summary", {})
            logger.info(
                "Route results: %d/%d online (%.1f%% reliability, avg latency: %.1fms)",
                summary.get("online_routes", 0),
                summary.get("total_routes", 0),
                summary.get("reliability_pct", 0.0),
                summary.get("avg_latency_ms", 0.0),
            )

            # 4. Assemble and transmit report
            try:
                payload = reporter.build_payload(geo_data=geo_info, routes_data=routes_data)
                reporter.send_report(payload)
            except Exception as exc:
                logger.error("Failed to build or transmit report: %s", exc)

            # 5. Sleep until next cycle, cleanly interruptible by shutdown_event
            elapsed = time.monotonic() - loop_start
            sleep_time = max(1.0, config.polling_interval - elapsed)
            logger.debug("Cycle completed in %.2fs. Sleeping for %.1fs...", elapsed, sleep_time)
            if shutdown_event.wait(timeout=sleep_time):
                break

    finally:
        logger.info("Shutting down supervisor...")
        supervisor.stop()
        logger.info("DinoVoyager Checker stopped cleanly.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
