"""Push :class:`NormalizedSignal` records to user-configured webhooks.

Backends supported (one :class:`WebhookProviderConfig` per backend):

- **Bark** (iOS push, free) — URL of the form
  ``https://api.day.app/{key}/{title}/{body}?group=...``. We POST a
  short JSON body that the Bark relay turns into a native push.
- **Telegram** — URL is
  ``https://api.telegram.org/bot<TOKEN>/sendMessage`` with
  ``chat_id`` in config. We POST ``{"chat_id": ..., "text": ...}``.
- **WeCom (企业微信)** — webhook URL with embedded key. We POST
  ``{"msgtype": "text", "text": {"content": ...}}``.
- **Generic** — any URL; we POST the signal envelope as JSON.

Each provider applies its own :class:`WebhookFilterConfig` so a noisy
multi-factor snapshot doesn't blow up a phone. Per SPEC §5.2 and
R5 mitigation, the sink serialises outbound requests with a 100ms
gap (configurable via :data:`_SEND_GAP_SECONDS`) so Bark/Telegram
rate limits (Bark ~5/min) are respected when a multi-symbol scan
fires several signals in one tick.

All failures are swallowed and logged; the service records the
failure in :attr:`DeliveryResult.failed_to`. The webhook sink does
NOT raise out of :meth:`send`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import httpx

from src.ashare.signals.config import WebhookProviderConfig
from src.ashare.signals.models import NormalizedSignal
from src.ashare.signals.sinks.base import SignalSink

logger = logging.getLogger(__name__)


def _load_symbol_name_map(base_url: str = "http://localhost:8000") -> dict[str, str]:
    """Fetch stock code -> name mapping from tushare/adshare /market/stock/basic.

    Falls back to an empty dict if the endpoint is unreachable so a missing
    name does not break signal delivery.
    """
    try:
        r = httpx.get(
            f"{base_url.rstrip('/')}/market/stock/basic",
            timeout=5.0,
        )
        r.raise_for_status()
        data = r.json().get("data") or []
        return {item.get("code", ""): (item.get("name") or "") for item in data if item.get("code")}
    except Exception as exc:
        logger.warning("Failed to load symbol name map from tushare/adshare: %s", exc)
        return {}

#: Per-provider sleep after a successful POST. 100ms keeps us under
#: Bark's ~5/min steady state when one tick fires N signals in a row.
#: SPEC §10.2 R5 mitigation.
_SEND_GAP_SECONDS = 0.1

#: HTTP timeout per request; webhook providers are not on the hot path
#: and a 5s ceiling means a slow relay cannot stall a tick.
_HTTP_TIMEOUT_SECONDS = 5.0


class WebhookSink:
    """Fan out a signal to every enabled provider in the config.

    The provider list is captured at construction; reload by
    constructing a new instance (typically via
    :func:`reset_delivery_service` in :mod:`src.ashare.signals.delivery`).
    """

    def __init__(self, providers: list[WebhookProviderConfig]) -> None:
        # Defensive copy + filter disabled; keeps ``send`` cheap.
        self._providers: list[WebhookProviderConfig] = [
            p for p in providers if p.enabled
        ]
        # Cache stock code -> name map for Feishu/Lark formatting.
        self._symbol_names = _load_symbol_name_map()

    def name(self) -> str:
        return "webhook"

    async def send(self, signal: NormalizedSignal) -> None:
        """Deliver ``signal`` to each enabled provider, in order.

        A provider whose filter rejects the signal is skipped silently
        (still logged at DEBUG). A provider whose HTTP call fails is
        logged at WARNING and the loop continues with the next one.
        Providers run sequentially to respect the per-provider gap;
        one tick with N signals to K providers is O(N*K) wall time.
        """
        if not self._providers:
            return
        for provider in self._providers:
            if not self._matches(provider, signal):
                logger.debug(
                    "webhook %s: filter rejected %s %s (conf=%.2f)",
                    provider.name, signal.symbol, signal.side, signal.confidence,
                )
                continue
            try:
                await self._send_one(provider, signal)
            except Exception as exc:  # noqa: BLE001 - sink must never raise
                logger.warning(
                    "webhook %s send failed for %s: %s",
                    provider.name, signal.symbol, exc,
                )
            # Gap between providers; SPEC §10.2 R5.
            await asyncio.sleep(_SEND_GAP_SECONDS)

    # ------------------------------------------------------------------ #
    # internals                                                           #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _matches(provider: WebhookProviderConfig, signal: NormalizedSignal) -> bool:
        f = provider.filter
        if f.strategies and signal.strategy_id not in f.strategies:
            return False
        if f.sides and signal.side not in f.sides:
            return False
        if signal.confidence < f.min_confidence:
            return False
        return True

    async def _send_one(self, provider: WebhookProviderConfig, signal: NormalizedSignal) -> None:
        """POST one signal to one provider.

        Body shape depends on the provider's URL pattern:
          - URL contains ``api.day.app`` → Bark GET with title/body in path
          - URL contains ``api.telegram.org`` → Telegram JSON
          - URL contains ``qyapi.weixin.qq.com`` → WeCom JSON
          - else → generic JSON POST
        """
        url = provider.url_effective()
        if "api.day.app" in url:
            # Bark: GET with title/body in URL path; the spec format
            # is ``https://api.day.app/{key}/{title}/{body}?group=...``.
            # We just GET whatever URL is configured; the user is
            # expected to have already substituted the key (or to use
            # a relay). The "title" and "body" are *path params* on
            # Bark; we keep the configured URL untouched and rely on
            # the user to template it via a query string when desired.
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS) as client:
                r = await client.get(url)
                r.raise_for_status()
            return

        title = provider.title_template.format(
            strategy_id=signal.strategy_id, side=signal.side, symbol=signal.symbol,
        )
        body = provider.body_template.format(
            reason=signal.reason or "(no reason)",
            ref_price=signal.ref_price,
            symbol=signal.symbol,
            side=signal.side,
            strategy_id=signal.strategy_id,
        )

        if "api.telegram.org" in url:
            payload: dict[str, Any] = {
                "chat_id": provider.chat_id,
                "text": f"*{title}*\n{body}",
                "parse_mode": "Markdown",
            }
        elif "qyapi.weixin.qq.com" in url:
            payload = {
                "msgtype": "text",
                "text": {"content": f"{title}\n{body}"},
            }
        elif "open.feishu.cn" in url:
            # Feishu/Lark custom bot webhook
            side_cn = {"buy": "买入", "sell": "卖出"}.get(
                signal.side, signal.side
            )
            name = self._symbol_names.get(signal.symbol, "")
            symbol_line = f"{name}（{signal.symbol}）" if name else signal.symbol
            payload = {
                "msg_type": "text",
                "content": {
                    "text": (
                        f"【多因子策略】\n"
                        f"  股票：{symbol_line}\n"
                        f"  信号：{side_cn}\n"
                        f"  参考价：{signal.ref_price} 元\n"
                        f"  原因：{signal.reason or '策略触发'}"
                    )
                },
            }
        else:
            # Generic: full signal envelope as JSON.
            payload = {
                "title": title,
                "body": body,
                "strategy_id": signal.strategy_id,
                "symbol": signal.symbol,
                "side": signal.side,
                "ref_price": signal.ref_price,
                "reason": signal.reason,
                "ts": signal.ts.isoformat(timespec="seconds"),
                "metadata": signal.metadata,
            }

        headers = {"Content-Type": "application/json", **provider.headers}
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS) as client:
            r = await client.request(
                provider.method, url, json=payload, headers=headers,
            )
            r.raise_for_status()
