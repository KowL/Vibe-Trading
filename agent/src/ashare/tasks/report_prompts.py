"""Prompt templates for A-share market reports.

System prompts synthesize conventions from the project's skills:
- report-generate: professional research report structure and terminology.
- ashare-limitup: limit-up ladder / seal amount / broken board analysis.
- sentiment-analysis: market sentiment gauges and position sizing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Any


class ReportKind(str, Enum):
    OPEN = "open"
    CLOSE = "close"
    WEEKLY = "weekly"


KIND_LABELS_BY_VALUE: dict[str, str] = {
    "open": "A股开盘报告",
    "close": "A股收盘复盘",
    "weekly": "A股周度复盘",
}


SYSTEM_PROMPT = """你是一名专业的 A 股量化策略分析师，擅长基于涨停梯队、板块热度、指数情绪和资金流向数据撰写盘前/盘后复盘报告。

你的任务是根据用户提供的结构化市场指标，输出一段专业的 Markdown 分析正文。报告必须包含以下固定章节：

## 摘要
3-5 条 bullet points，每条 1-2 句话，给出最核心的结论（多空态度必须明确：偏多 / 偏空 / 震荡）。

## 核心观点
分 2-4 个小节，每个小节有一个明确标题。结合数据说明主线板块、涨停梯队、指数情绪的变化逻辑。要求“因为 A 数据，所以 B 判断，因此 C 操作”。

## 情绪研判
基于涨停家数、炸板率、封单强度、最高连板、指数涨跌等指标，判断当前市场情绪所处阶段（冰点 / 修复 / 升温 / 亢奋 / 退潮），并说明理由。

## 风险提示
至少列出 3 条具体风险，每条包含触发条件和可能影响。可涉及：高位连板分化、板块退潮、指数缩量、情绪过热、政策监管等。

## 操作建议
给出明确的仓位与方向建议，例如：
- 可打板 / 可低吸的板块或标的类型（不荐具体代码，可提龙头风格）。
- 建议观望的信号。
- 建议回避的情形。

---

写作规范：
- 使用专业研报术语：用“企稳 / 分化 / 退潮 / 修复”代替“涨 / 跌”。
- 所有观点必须有数据支撑，禁止空泛描述。
- 不预测具体点位，不说“一定”“必然”。
- 不编造不存在的数据；若某项指标缺失，直接忽略，不要脑补。
- 报告末尾不需要写免责声明（数据附录中会统一追加）。
"""


OPEN_SYSTEM_APPENDIX = """
这是开盘报告，数据基础是上一交易日的收盘复盘。请重点分析：
1. 隔夜/盘前情绪延续性：昨日主线今日是否值得继续关注。
2. 集合竞价阶段的高低标溢价预期。
3. 今日仓位建议与重点关注方向。
"""

CLOSE_SYSTEM_APPENDIX = """
这是收盘复盘，数据基础是当日完整交易数据。请重点分析：
1. 当日盘面主线与支线轮动。
2. 涨停梯队结构（高低标、中位股风险）。
3. 情绪周期位置与次日策略。
"""

WEEKLY_SYSTEM_APPENDIX = """
这是周度复盘，数据基础是本周全部交易日的聚合数据。请重点分析：
1. 本周指数与主线的整体表现。
2. 连板高度与板块热度的周度变化趋势。
3. 下周展望与潜在风险点。
"""


def build_system_prompt(kind: ReportKind) -> str:
    """Return the system prompt for the requested report kind."""
    appendix = {
        ReportKind.OPEN: OPEN_SYSTEM_APPENDIX,
        ReportKind.CLOSE: CLOSE_SYSTEM_APPENDIX,
        ReportKind.WEEKLY: WEEKLY_SYSTEM_APPENDIX,
    }[kind]
    return SYSTEM_PROMPT + "\n" + appendix


def _format_date(d: date | str | None) -> str | None:
    if isinstance(d, date):
        return d.isoformat()
    return d


def build_user_prompt(
    kind: ReportKind,
    metrics: dict[str, Any],
    trade_date: date,
    week_start: date | None = None,
    week_end: date | None = None,
) -> str:
    """Build the user prompt from collected metrics.

    Args:
        kind: Report type.
        metrics: Serialized market metrics (JSON-safe dict).
        trade_date: Primary trade date for the report.
        week_start: Start of the week (weekly reports only).
        week_end: End of the week (weekly reports only).
    """
    label = KIND_LABELS_BY_VALUE.get(kind.value, kind.value)
    date_info = f"报告日期: {trade_date.isoformat()}"
    if kind == ReportKind.WEEKLY and week_start and week_end:
        date_info += f"\n统计周期: {week_start.isoformat()} ~ {week_end.isoformat()}"

    return f"""请为以下 A 股市场指标撰写一份 **{label}** 的分析正文。

{date_info}

```json
{metrics}
```

要求：
- 仅输出 Markdown 分析正文（从 ## 摘要 开始到 ## 操作建议 结束）。
- 不要输出代码块包裹的 JSON 解释。
- 不要输出任何与报告无关的闲聊。
- 报告中引用的所有数字必须与上述 JSON 完全一致。
"""


@dataclass
class PromptBundle:
    """Convenience container for a single LLM call."""

    system: str
    user: str
