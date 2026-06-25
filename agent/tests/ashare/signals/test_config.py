"""Unit tests for the signals config loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.ashare.signals.config import (
    ConfigError, WebhookProviderConfig, WebhookSinkConfig,
    load_signals_config,
)


def test_missing_file_returns_defaults(tmp_path: Path) -> None:
    cfg = load_signals_config(path=tmp_path / "absent.yaml")
    assert cfg.dedup.cooldown_seconds == 1800
    assert cfg.sinks.webhook.enabled is False
    assert cfg.sinks.webhook.providers == []


def test_valid_yaml_parses(tmp_path: Path) -> None:
    f = tmp_path / "signals.yaml"
    f.write_text("""
dedup:
  cooldown_seconds: 60
sinks:
  webhook:
    enabled: true
    providers:
      - name: bark
        url: "https://api.day.app/ABC/X/Y"
        filter:
          min_confidence: 0.7
          strategies: [my_bollinger]
          sides: [buy, sell]
      - name: tg
        webhook_url: "https://api.telegram.org/botX/sendMessage"
        chat_id: "123"
        enabled: false
""")
    cfg = load_signals_config(path=f)
    assert cfg.dedup.cooldown_seconds == 60
    assert len(cfg.sinks.webhook.providers) == 2
    bark = cfg.sinks.webhook.providers[0]
    assert bark.name == "bark"
    assert bark.filter.min_confidence == 0.7
    tg = cfg.sinks.webhook.providers[1]
    assert tg.chat_id == "123"
    assert tg.enabled is False


def test_unknown_field_rejected(tmp_path: Path) -> None:
    f = tmp_path / "signals.yaml"
    f.write_text("dedup:\n  typo: 1\n")
    with pytest.raises(ConfigError):
        load_signals_config(path=f)


def test_negative_cooldown_rejected(tmp_path: Path) -> None:
    f = tmp_path / "signals.yaml"
    f.write_text("dedup:\n  cooldown_seconds: -5\n")
    with pytest.raises(ConfigError):
        load_signals_config(path=f)


def test_provider_without_url_rejected() -> None:
    with pytest.raises(Exception):
        WebhookProviderConfig(name="x")


def test_provider_name_normalised_to_lowercase() -> None:
    p = WebhookProviderConfig(name="BARK", url="http://x")
    assert p.name == "bark"


def test_url_effective_returns_configured_url() -> None:
    p = WebhookProviderConfig(name="x", url="http://a.test/")
    assert p.url_effective() == "http://a.test/"
    p2 = WebhookProviderConfig(name="x", webhook_url="http://b.test/")
    assert p2.url_effective() == "http://b.test/"


def test_provider_without_url_rejected_at_construction() -> None:
    """Construction must fail; ``url_effective`` is only callable on a valid instance."""
    with pytest.raises(Exception):
        WebhookProviderConfig(name="x", url=None, webhook_url=None)


def test_filter_min_confidence_out_of_range_rejected() -> None:
    from src.ashare.signals.config import WebhookFilterConfig
    with pytest.raises(Exception):
        WebhookFilterConfig(min_confidence=1.5)
