"""Unit tests for checker.config module (covering all specifications and edge cases)."""

from __future__ import annotations

import os
from typing import Any, Dict
import pytest

from checker.config import Config, ConfigError, load_config


# --- Explorer Base Tests ---

def test_load_from_environment(valid_env: None) -> None:
    """Tests loading complete configuration exclusively from environment variables."""
    cfg = load_config([])
    assert cfg.subscription_url == "https://app.dinovoyager.net/api/sub/valid_token"
    assert cfg.polling_interval == 60
    assert cfg.api_url == "https://app.dinovoyager.net"
    assert cfg.api_key == "dvc_live_testkey1234567890abcdef"
    assert cfg.log_level == "INFO"
    assert cfg.proxy_timeout == 10


def test_load_from_cli_args(clean_env: None) -> None:
    """Tests loading complete configuration from CLI flags with empty environment."""
    cli_args = [
        "--subscription-url", "https://cli.url/sub",
        "-i", "45",
        "--api-url", "https://cli.url",
        "-k", "dvc_live_cli_key",
        "--name", "My-Home-Node",
        "--log-level", "DEBUG",
        "--timeout", "15",
    ]
    cfg = load_config(cli_args)
    assert cfg.subscription_url == "https://cli.url/sub"
    assert cfg.polling_interval == 45
    assert cfg.api_url == "https://cli.url"
    assert cfg.api_key == "dvc_live_cli_key"
    assert cfg.checker_name == "My-Home-Node"
    assert cfg.log_level == "DEBUG"
    assert cfg.proxy_timeout == 15


def test_cli_overrides_environment(valid_env: None) -> None:
    """Tests that CLI flags strictly take precedence over environment variables."""
    cli_args = [
        "--interval", "90",
        "--api-url", "https://override.dinovoyager.net",
    ]
    cfg = load_config(cli_args)
    # Overridden values:
    assert cfg.polling_interval == 90
    assert cfg.api_url == "https://override.dinovoyager.net"
    # Preserved values from ENV:
    assert cfg.subscription_url == "https://app.dinovoyager.net/api/sub/valid_token"
    assert cfg.api_key == "dvc_live_testkey1234567890abcdef"


@pytest.mark.parametrize("missing_param", [
    "SUBSCRIPTION_URL",
    "API_URL",
    "API_KEY",
])
def test_missing_required_params_raises_error(
    clean_env: None,
    monkeypatch: pytest.MonkeyPatch,
    missing_param: str,
) -> None:
    """Tests that omission of any required parameter triggers ConfigError."""
    full_env = {
        "SUBSCRIPTION_URL": "https://sub.url",
        "POLLING_INTERVAL": "60",
        "API_URL": "https://api.url",
        "API_KEY": "key123",
    }
    del full_env[missing_param]
    for k, v in full_env.items():
        monkeypatch.setenv(k, v)

    with pytest.raises(ConfigError) as exc_info:
        load_config([])
    err_text = str(exc_info.value).lower()
    assert missing_param.lower() in err_text or "required" in err_text


def test_interval_lower_bound_validation(valid_env: None) -> None:
    """Tests that polling interval below 15 seconds is rejected."""
    with pytest.raises(ConfigError) as exc_info:
        load_config(["--interval", "10"])
    assert "15" in str(exc_info.value)


def test_invalid_interval_type(valid_env: None) -> None:
    """Tests that non-integer interval input is rejected."""
    with pytest.raises(ConfigError):
        load_config(["--interval", "not_a_number"])


def test_invalid_url_scheme(valid_env: None) -> None:
    """Tests rejection of invalid schemes like ftp:// or plain text."""
    with pytest.raises(ConfigError) as exc_info:
        load_config(["--api-url", "ftp://my.server.com"])
    assert "scheme" in str(exc_info.value).lower() or "http" in str(exc_info.value).lower()


def test_trailing_slash_stripped_from_api_url(valid_env: None) -> None:
    """Tests that trailing slashes are automatically stripped from API_URL."""
    cfg = load_config(["--api-url", "https://app.dinovoyager.net///"])
    assert cfg.api_url == "https://app.dinovoyager.net"


def test_log_level_normalization(valid_env: None) -> None:
    """Tests normalization of log level strings like 'warn' to 'WARNING'."""
    cfg = load_config(["--log-level", "warn"])
    assert cfg.log_level == "WARNING"


def test_exit_on_error_calls_sys_exit(clean_env: None) -> None:
    """Tests that exit_on_error=True prints error and calls sys.exit(1)."""
    with pytest.raises(SystemExit) as exc_info:
        load_config([], exit_on_error=True)
    assert exc_info.value.code == 1


# --- 20 Tests Specified in teamwork_spec_miner_e2e_1/handoff.md § 5.3 ---

def test_config_load_from_cli_only(clean_env):
    """1. All 4 required params via CLI flags."""
    cfg = load_config(
        ["-s", "https://sub.url/token", "-i", "30", "-u", "https://api.url", "-k", "key123"]
    )
    assert cfg.subscription_url == "https://sub.url/token"
    assert cfg.polling_interval == 30
    assert cfg.api_url == "https://api.url"
    assert cfg.api_key == "key123"


def test_config_load_from_cli_long_flags(clean_env):
    """2. All 4 required params via long flags."""
    cfg = load_config(
        [
            "--subscription-url", "https://sub.url/token",
            "--interval", "45",
            "--api-url", "https://api.url",
            "--api-key", "key123",
        ]
    )
    assert cfg.subscription_url == "https://sub.url/token"
    assert cfg.polling_interval == 45
    assert cfg.api_url == "https://api.url"
    assert cfg.api_key == "key123"


def test_config_load_from_env_only(clean_env, valid_env):
    """3. All 4 required params via os.environ."""
    cfg = load_config([])
    assert cfg.subscription_url == "https://app.dinovoyager.net/api/sub/valid_token"
    assert cfg.polling_interval == 60
    assert cfg.api_url == "https://app.dinovoyager.net"
    assert cfg.api_key == "dvc_live_testkey1234567890abcdef"


def test_config_cli_precedence_over_env(clean_env, valid_env):
    """4. CLI overrides ENV when both present."""
    cfg = load_config(["--interval", "120", "--api-key", "cli_override_key"])
    assert cfg.polling_interval == 120
    assert cfg.api_key == "cli_override_key"
    assert cfg.subscription_url == "https://app.dinovoyager.net/api/sub/valid_token"
    assert cfg.api_url == "https://app.dinovoyager.net"


def test_config_mixed_sources(clean_env, monkeypatch):
    """5. 2 params from ENV, 2 from CLI."""
    monkeypatch.setenv("SUBSCRIPTION_URL", "https://env.url/sub")
    monkeypatch.setenv("API_KEY", "env_secret_key")

    cfg = load_config(["--interval", "90", "--api-url", "https://custom.api"])
    assert cfg.subscription_url == "https://env.url/sub"
    assert cfg.api_key == "env_secret_key"
    assert cfg.polling_interval == 90
    assert cfg.api_url == "https://custom.api"


def test_config_missing_subscription_url(clean_env):
    """6. Missing SUBSCRIPTION_URL raises ConfigError."""
    with pytest.raises(ConfigError) as exc_info:
        load_config(["-i", "30", "-u", "https://api.url", "-k", "key123"])
    assert "subscription" in str(exc_info.value).lower()


def test_config_missing_polling_interval(clean_env):
    """7. Missing POLLING_INTERVAL raises ConfigError."""
    with pytest.raises(ConfigError) as exc_info:
        load_config(["-s", "https://sub.url/token", "-u", "https://api.url", "-k", "key123"])
    assert "interval" in str(exc_info.value).lower()


def test_config_missing_api_url(clean_env):
    """8. Missing API_URL raises ConfigError."""
    with pytest.raises(ConfigError) as exc_info:
        load_config(["-s", "https://sub.url/token", "-i", "30", "-k", "key123"])
    assert "api-url" in str(exc_info.value).lower() or "api_url" in str(exc_info.value).lower()


def test_config_missing_api_key(clean_env):
    """9. Missing API_KEY raises ConfigError."""
    with pytest.raises(ConfigError) as exc_info:
        load_config(["-s", "https://sub.url/token", "-i", "30", "-u", "https://api.url"])
    assert "api-key" in str(exc_info.value).lower() or "api_key" in str(exc_info.value).lower()


def test_config_missing_all_required_params(clean_env):
    """10. Completely empty configuration raises ConfigError listing missing items."""
    with pytest.raises(ConfigError) as exc_info:
        load_config([])
    err_msg = str(exc_info.value).lower()
    assert "subscription" in err_msg
    assert "interval" in err_msg
    assert "api-url" in err_msg or "api_url" in err_msg
    assert "api-key" in err_msg or "api_key" in err_msg


def test_config_interval_lower_bound_rejected(clean_env):
    """11. Interval < 15 seconds is rejected."""
    with pytest.raises(ConfigError) as exc_info:
        load_config([
            "-s", "https://sub.url/token",
            "-i", "14",
            "-u", "https://api.url",
            "-k", "key123",
        ])
    assert "at least 15 seconds" in str(exc_info.value)


def test_config_interval_boundary_exact_15(clean_env):
    """12. Interval exactly 15 seconds is accepted."""
    cfg = load_config([
        "-s", "https://sub.url/token",
        "-i", "15",
        "-u", "https://api.url",
        "-k", "key123",
    ])
    assert cfg.polling_interval == 15


def test_config_interval_boundary_16(clean_env):
    """13. Interval 16 seconds is accepted."""
    cfg = load_config([
        "-s", "https://sub.url/token",
        "-i", "16",
        "-u", "https://api.url",
        "-k", "key123",
    ])
    assert cfg.polling_interval == 16


def test_config_interval_zero_and_negative(clean_env):
    """14. Interval 0 or negative integer is rejected."""
    with pytest.raises(ConfigError):
        load_config([
            "-s", "https://sub.url/token",
            "-i", "0",
            "-u", "https://api.url",
            "-k", "key123",
        ])

    with pytest.raises(ConfigError):
        load_config([
            "-s", "https://sub.url/token",
            "-i", "-60",
            "-u", "https://api.url",
            "-k", "key123",
        ])


def test_config_interval_non_numeric(clean_env):
    """15. Non-integer string for interval is rejected."""
    with pytest.raises(ConfigError):
        load_config([
            "-s", "https://sub.url/token",
            "-i", "fast",
            "-u", "https://api.url",
            "-k", "key123",
        ])


def test_config_optional_defaults(clean_env):
    """16. Optional parameters have sensible defaults."""
    cfg = load_config([
        "-s", "https://sub.url/token",
        "-i", "30",
        "-u", "https://api.url",
        "-k", "key123",
    ])
    assert cfg.checker_name is None
    assert cfg.proxy_timeout == 10
    assert cfg.log_level == "INFO"


def test_config_optional_custom_cli(clean_env):
    """17. Custom optional params via CLI."""
    cfg = load_config([
        "-s", "https://sub.url/token",
        "-i", "30",
        "-u", "https://api.url",
        "-k", "key123",
        "--name", "Home-London",
        "--timeout", "25",
        "--log-level", "DEBUG",
    ])
    assert cfg.checker_name == "Home-London"
    assert cfg.proxy_timeout == 25
    assert cfg.log_level == "DEBUG"


def test_config_optional_custom_env(clean_env, valid_env, monkeypatch):
    """18. Custom optional params via ENV."""
    monkeypatch.setenv("CHECKER_NAME", "Home-DE")
    monkeypatch.setenv("PROXY_TIMEOUT", "30")
    monkeypatch.setenv("LOG_LEVEL", "WARN")

    cfg = load_config([])
    assert cfg.checker_name == "Home-DE"
    assert cfg.proxy_timeout == 30
    assert cfg.log_level == "WARNING"


def test_config_api_url_trailing_slash_normalization(clean_env):
    """19. Trailing slash on API_URL is stripped cleanly."""
    cfg = load_config([
        "-s", "https://sub.url/token",
        "-i", "30",
        "-u", "https://app.dinovoyager.net/",
        "-k", "key123",
    ])
    assert cfg.api_url == "https://app.dinovoyager.net"


def test_config_url_scheme_validation(clean_env):
    """20. Non-HTTP/HTTPS protocol scheme is rejected."""
    with pytest.raises(ConfigError) as exc_info:
        load_config([
            "-s", "https://sub.url/token",
            "-i", "30",
            "-u", "ftp://bad-url",
            "-k", "key123",
        ])
    assert "scheme" in str(exc_info.value).lower()
