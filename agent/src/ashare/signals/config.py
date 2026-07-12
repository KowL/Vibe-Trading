"""YAML-driven configuration for the signal delivery subsystem.

The file lives at ``~/.vibe-trading/ashare/signals.yaml`` and is
overlaid on the built-in defaults below. Every field is optional;
missing keys fall back to the defaults and a missing file just
returns the defaults.

Top-level shape (SPEC §5.1)::

    dedup:
      cooldown_seconds: 1800

    sinks:
      local:
        enabled: true
        output_dir: ~/.vibe-trading/ashare/signals

      sse:
        enabled: true
        event_bus_session: ashare_broadcast

      webhook:
        enabled: true
        providers:
          - name: bark
            url: "https://api.day.app/{key}/{title}/{body}?group=vibe-trading"
            enabled: true
            filter:
              min_confidence: 0.7
              strategies: [my_bollinger]
          - name: telegram
            webhook_url: "https://api.telegram.org/bot<TOKEN>/sendMessage"
            chat_id: "<CHAT_ID>"
            enabled: false

    audit:
      enabled: true
      log_path: ~/.vibe-trading/ashare/audit/signals.jsonl

The schema is intentionally small and uses ``pydantic`` for validation
so an operator typo surfaces as a clear error message instead of a
silent fallback.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - dev / minimal envs
    yaml = None  # type: ignore[assignment]

from pydantic import BaseModel, ConfigDict, Field, field_validator

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Defaults                                                                    #
# --------------------------------------------------------------------------- #


DEFAULT_CONFIG_PATH = Path.home() / ".vibe-trading" / "ashare" / "signals.yaml"


# --------------------------------------------------------------------------- #
# Schemas                                                                     #
# --------------------------------------------------------------------------- #


class _StrictModel(BaseModel):
    """Forbid unknown keys so config typos surface as errors."""

    model_config = ConfigDict(extra="forbid")


class DedupConfig(_StrictModel):
    cooldown_seconds: int = 1800

    @field_validator("cooldown_seconds")
    @classmethod
    def _cooldown_non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError("cooldown_seconds must be >= 0")
        return v


class _SinkBase(_StrictModel):
    enabled: bool = True


class LocalSinkConfig(_SinkBase):
    output_dir: str = str(Path.home() / ".vibe-trading" / "ashare" / "signals")


class SSESinkConfig(_SinkBase):
    event_bus_session: str = "ashare_broadcast"


class WebhookFilterConfig(_StrictModel):
    """Per-provider filter so a noisy multi-factor output can be silenced.

    Empty ``strategies`` list == accept all. ``min_confidence`` defaults
    to 0 (no filter). ``sides`` defaults to ``["buy", "sell"]``; ``hold``
    and ``watch`` are state-tracking signals that often spam a phone.
    """

    min_confidence: float = 0.0
    strategies: list[str] = Field(default_factory=list)
    sides: list[str] = Field(default_factory=lambda: ["buy", "sell"])

    @field_validator("min_confidence")
    @classmethod
    def _conf_in_range(cls, v: float) -> float:
        if v < 0 or v > 1:
            raise ValueError("min_confidence must be in [0, 1]")
        return v


class WebhookProviderConfig(_StrictModel):
    """One webhook provider (Bark / Telegram / WeCom / generic POST).

    At least one of ``url`` or ``webhook_url`` must be set; we treat them
    as the same field. The HTTP method defaults to POST; the body is
    JSON-encoded signal data.
    """

    name: str
    enabled: bool = True
    url: str | None = None
    webhook_url: str | None = None
    method: str = "POST"
    headers: dict[str, str] = Field(default_factory=dict)
    # Telegram-specific (optional; ignored for Bark / WeCom)
    chat_id: str | None = None
    # Per-provider filter
    filter: WebhookFilterConfig = Field(default_factory=WebhookFilterConfig)
    # Send body wrapping template; for Bark we just substitute {title}/{body}.
    title_template: str = "[{strategy_id}] {side} {symbol}"
    body_template: str = "{reason} ref={ref_price}"

    @field_validator("name")
    @classmethod
    def _name_required(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("provider.name must be non-empty")
        return v.strip().lower()

    @field_validator("method")
    @classmethod
    def _method_uppercase(cls, v: str) -> str:
        return v.upper() if v else "POST"

    def model_post_init(self, __context: Any) -> None:
        # Enforce "at least one URL" without a custom @model_validator
        # (which would have to handle the *args dance across pydantic v1/v2).
        if not self.url and not self.webhook_url:
            raise ValueError(
                f"webhook provider '{self.name}' must set url or webhook_url"
            )

    def url_effective(self) -> str:
        """Return whichever URL field is set, or raise."""
        u = self.url or self.webhook_url
        if not u:
            raise ValueError(
                f"webhook provider '{self.name}' has no url/webhook_url"
            )
        return u


class WebhookSinkConfig(_SinkBase):
    enabled: bool = False  # opt-in by default; safer than broadcasting
    providers: list[WebhookProviderConfig] = Field(default_factory=list)


class AuditConfig(_StrictModel):
    enabled: bool = True
    log_path: str = str(
        Path.home() / ".vibe-trading" / "ashare" / "audit" / "signals.jsonl"
    )


class SinksConfig(_StrictModel):
    """Bundles the per-sink sub-configs."""

    local: LocalSinkConfig = Field(default_factory=LocalSinkConfig)
    sse: SSESinkConfig = Field(default_factory=SSESinkConfig)
    webhook: WebhookSinkConfig = Field(default_factory=WebhookSinkConfig)


class SignalsConfig(_StrictModel):
    """Top-level config."""

    dedup: DedupConfig = Field(default_factory=DedupConfig)
    sinks: SinksConfig = Field(default_factory=SinksConfig)
    audit: AuditConfig = Field(default_factory=AuditConfig)


# --------------------------------------------------------------------------- #
# Loader                                                                      #
# --------------------------------------------------------------------------- #


def load_signals_config(
    path: Path | str | None = None,
) -> SignalsConfig:
    """Load and validate the signals config from disk.

    Args:
        path: Explicit config file. Defaults to
            ``~/.vibe-trading/ashare/signals.yaml``. When the file is
            missing the function returns :class:`SignalsConfig` filled
            with defaults (no warning beyond debug, because the
            common case is "no config yet").

    Returns:
        The validated :class:`SignalsConfig`. A malformed file raises
        :class:`ConfigError` so the operator sees a clear error
        instead of silent fallback.
    """
    cfg_path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    if not cfg_path.is_file():
        logger.debug("signals config not found at %s; using defaults", cfg_path)
        return SignalsConfig()
    if yaml is None:
        raise ConfigError(
            "PyYAML is required to load signals.yaml; install pyyaml"
        )
    try:
        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError) as exc:
        raise ConfigError(f"signals config {cfg_path} is unreadable: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(
            f"signals config {cfg_path} root must be a mapping, got {type(raw).__name__}"
        )
    try:
        return SignalsConfig.model_validate(raw)
    except Exception as exc:  # pydantic.ValidationError is a subclass
        raise ConfigError(f"signals config {cfg_path} failed validation: {exc}") from exc


class ConfigError(ValueError):
    """Raised when the signals config is malformed or missing deps."""
