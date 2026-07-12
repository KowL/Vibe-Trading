"""LLM-backed analysis generation for A-share market reports."""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import date
from typing import Any

from src.providers.llm import build_llm
from src.ashare.tasks.report_prompts import (
    PromptBundle,
    ReportKind,
    build_system_prompt,
    build_user_prompt,
)

logger = logging.getLogger(__name__)


class ReportLLMError(RuntimeError):
    """Raised when the LLM call for report analysis fails."""


@dataclass
class AnalysisResult:
    """Result of an LLM analysis call."""

    markdown: str
    model: str
    duration_seconds: float
    input_tokens: int | None
    output_tokens: int | None


def _extract_usage(usage: Any) -> tuple[int | None, int | None]:
    """Normalize LangChain usage metadata to (input, output)."""
    if usage is None:
        return None, None
    if isinstance(usage, dict):
        return (
            usage.get("input_tokens") or usage.get("prompt_tokens"),
            usage.get("output_tokens") or usage.get("completion_tokens"),
        )
    try:
        usage_dict = dict(usage)  # type: ignore[call-overload]
        return (
            usage_dict.get("input_tokens") or usage_dict.get("prompt_tokens"),
            usage_dict.get("output_tokens") or usage_dict.get("completion_tokens"),
        )
    except (TypeError, ValueError):
        return None, None


import re

_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def _normalize_markdown(raw: str) -> str:
    """Strip leading/trailing whitespace, code fences, and thinking blocks."""
    text = raw.strip()
    # Remove reasoning/thinking blocks emitted by models such as MiniMax.
    text = _THINK_BLOCK_RE.sub("", text)
    if text.startswith("```markdown"):
        text = text[len("```markdown") :]
    if text.startswith("```"):
        text = text[len("```") :]
    if text.endswith("```"):
        text = text[: -len("```")]
    return text.strip()


def build_prompt_bundle(
    kind: ReportKind,
    metrics: dict[str, Any],
    trade_date: date,
    week_start: date | None = None,
    week_end: date | None = None,
) -> PromptBundle:
    """Create system + user messages for a report analysis call."""
    return PromptBundle(
        system=build_system_prompt(kind),
        user=build_user_prompt(kind, metrics, trade_date, week_start, week_end),
    )


async def generate_report_analysis(
    kind: ReportKind,
    metrics: dict[str, Any],
    trade_date: date,
    week_start: date | None = None,
    week_end: date | None = None,
    model_name: str | None = None,
) -> AnalysisResult:
    """Generate the analytical markdown body for a market report.

    Args:
        kind: Report type.
        metrics: JSON-safe dict of market metrics.
        trade_date: Primary trade date.
        week_start: Week start (weekly reports only).
        week_end: Week end (weekly reports only).
        model_name: Optional override model; defaults to LANGCHAIN_MODEL_NAME.

    Returns:
        AnalysisResult with generated markdown and usage metadata.

    Raises:
        ReportLLMError: If the model is not configured or the call fails.
    """
    effective_model = (model_name or os.getenv("LANGCHAIN_MODEL_NAME", "")).strip()
    if not effective_model:
        raise ReportLLMError("LANGCHAIN_MODEL_NAME is not set")

    bundle = build_prompt_bundle(kind, metrics, trade_date, week_start, week_end)
    messages = [
        {"role": "system", "content": bundle.system},
        {"role": "user", "content": bundle.user},
    ]

    start = time.perf_counter()
    try:
        llm = build_llm(model_name=effective_model)
        response = await llm.ainvoke(messages)
    except Exception as exc:
        logger.warning("LLM analysis failed for %s report: %s", kind.value, exc)
        raise ReportLLMError(f"LLM analysis failed: {exc}") from exc

    duration = time.perf_counter() - start
    input_tokens, output_tokens = _extract_usage(getattr(response, "usage_metadata", None))
    content = getattr(response, "content", "")
    if not isinstance(content, str):
        content = str(content)

    markdown = _normalize_markdown(content)
    if not markdown:
        raise ReportLLMError("LLM returned empty analysis")

    return AnalysisResult(
        markdown=markdown,
        model=effective_model,
        duration_seconds=duration,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
