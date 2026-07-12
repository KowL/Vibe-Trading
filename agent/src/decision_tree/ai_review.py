"""AI 复盘模块 - 基于 LLM 的决策树生成"""
from typing import Dict, List, Optional, Any
from datetime import datetime

from .models import DecisionRule, RuleCondition, ActionType, ReviewInput, ReviewOutput
from .engine import DecisionTreeEngine


class AIReviewEngine:
    """AI 复盘引擎 - 根据交易记录生成决策树规则"""

    def __init__(self, engine: DecisionTreeEngine):
        self.engine = engine

    def analyze_trades(self, trades: List[Dict[str, Any]]) -> Dict[str, Any]:
        """分析交易记录，提取模式"""
        if not trades:
            return {"patterns": [], "mistakes": [], "lessons": []}

        # 统计盈亏
        profit_trades = [t for t in trades if t.get("pnl", 0) > 0]
        loss_trades = [t for t in trades if t.get("pnl", 0) <= 0]

        # 分析错误模式
        mistakes = []
        lessons = []

        for trade in loss_trades:
            reason = trade.get("loss_reason", "")
            if "追高" in reason or "买入" in reason:
                mistakes.append(f"退潮期追高: {trade.get('symbol', '')}")
                lessons.append("退潮期应降低仓位或空仓")
            if "止损" in reason or "扛单" in reason:
                mistakes.append(f"未及时止损: {trade.get('symbol', '')}")
                lessons.append("严格止损纪律，亏损-5%必须止损")
            if "仓位" in reason:
                mistakes.append(f"仓位过重: {trade.get('symbol', '')}")
                lessons.append("混沌期仓位不超过50%")

        # 分析成功模式
        for trade in profit_trades:
            reason = trade.get("profit_reason", "")
            if "龙头" in reason or "主升" in reason:
                lessons.append("主升期聚焦龙头，仓位80%")
            if "低吸" in reason:
                lessons.append("冰点期可以30%仓位试错")

        return {
            "patterns": [],
            "mistakes": list(set(mistakes)),
            "lessons": list(set(lessons)),
            "profit_count": len(profit_trades),
            "loss_count": len(loss_trades),
        }

    def generate_rules(self, review_input: ReviewInput) -> List[DecisionRule]:
        """根据复盘输入生成新规则"""
        new_rules = []

        # 分析交易记录
        analysis = self.analyze_trades(review_input.trades)

        # 根据错误生成防御性规则
        for mistake in analysis["mistakes"]:
            if "退潮期追高" in mistake:
                rule = DecisionRule(
                    id=f"rule_ai_{datetime.now().strftime('%Y%m%d%H%M%S')}_{len(new_rules)}",
                    name="退潮期禁止追高",
                    description=f"从复盘错误中提取: {mistake}",
                    conditions=[
                        RuleCondition(field="sentiment_cycle", operator="eq", value="退潮"),
                        RuleCondition(field="action", operator="eq", value="BUY"),
                    ],
                    action=ActionType.WATCH,
                    position_pct=0.0,
                    priority=1,  # 最高优先级
                    created_by="ai",
                    source="AI复盘生成"
                )
                new_rules.append(rule)

            if "未及时止损" in mistake:
                rule = DecisionRule(
                    id=f"rule_ai_{datetime.now().strftime('%Y%m%d%H%M%S')}_{len(new_rules)}",
                    name="强制止损纪律",
                    description=f"从复盘错误中提取: {mistake}",
                    conditions=[
                        RuleCondition(field="hold_loss_pct", operator="lte", value=-5.0),
                    ],
                    action=ActionType.STOP_LOSS,
                    position_pct=0.0,
                    priority=1,
                    created_by="ai",
                    source="AI复盘生成"
                )
                new_rules.append(rule)

            if "仓位过重" in mistake:
                rule = DecisionRule(
                    id=f"rule_ai_{datetime.now().strftime('%Y%m%d%H%M%S')}_{len(new_rules)}",
                    name="混沌期仓位控制",
                    description=f"从复盘错误中提取: {mistake}",
                    conditions=[
                        RuleCondition(field="sentiment_cycle", operator="in", value=["混沌", "混沌偏空"]),
                    ],
                    action=ActionType.WATCH,
                    position_pct=40.0,
                    priority=5,
                    created_by="ai",
                    source="AI复盘生成"
                )
                new_rules.append(rule)

        # 根据经验生成进攻性规则
        for lesson in analysis["lessons"]:
            if "主升期聚焦龙头" in lesson:
                rule = DecisionRule(
                    id=f"rule_ai_{datetime.now().strftime('%Y%m%d%H%M%S')}_{len(new_rules)}",
                    name="主升期龙头策略",
                    description=f"从复盘经验提取: {lesson}",
                    conditions=[
                        RuleCondition(field="sentiment_cycle", operator="eq", value="主升"),
                        RuleCondition(field="is_leader", operator="eq", value=True),
                    ],
                    action=ActionType.BUY,
                    position_pct=80.0,
                    priority=10,
                    created_by="ai",
                    source="AI复盘生成"
                )
                new_rules.append(rule)

            if "冰点期试错" in lesson:
                rule = DecisionRule(
                    id=f"rule_ai_{datetime.now().strftime('%Y%m%d%H%M%S')}_{len(new_rules)}",
                    name="冰点期试错策略",
                    description=f"从复盘经验提取: {lesson}",
                    conditions=[
                        RuleCondition(field="sentiment_cycle", operator="eq", value="冰点"),
                    ],
                    action=ActionType.BUY,
                    position_pct=30.0,
                    priority=10,
                    created_by="ai",
                    source="AI复盘生成"
                )
                new_rules.append(rule)

        return new_rules

    def review(self, review_input: ReviewInput) -> ReviewOutput:
        """
        执行复盘 - 分析交易记录并生成决策树更新

        完整版应该接入 LLM 进行深度分析
        """
        # 生成新规则
        new_rules = self.generate_rules(review_input)

        # 添加到引擎
        for rule in new_rules:
            self.engine.add_rule(rule)

        # 生成总结
        summary = self._generate_summary(review_input, new_rules)

        return ReviewOutput(
            review_id=f"review_{datetime.now().strftime('%Y%m%d%H%M%S')}",
            date=review_input.date,
            new_rules=new_rules,
            modified_rules=[],
            removed_rule_ids=[],
            summary=summary,
            confidence=0.75 if new_rules else 0.5
        )

    def _generate_summary(self, review_input: ReviewInput, new_rules: List[DecisionRule]) -> str:
        """生成复盘总结"""
        parts = []
        parts.append(f"## {review_input.date} 复盘总结")
        parts.append("")
        parts.append("### 市场概况")
        parts.append(review_input.market_summary)
        parts.append("")

        if review_input.mistakes:
            parts.append("### 错误分析")
            for m in review_input.mistakes:
                parts.append(f"- {m}")
            parts.append("")

        if review_input.lessons:
            parts.append("### 经验教训")
            for l in review_input.lessons:
                parts.append(f"- {l}")
            parts.append("")

        if new_rules:
            parts.append(f"### 生成规则 ({len(new_rules)}条)")
            for r in new_rules:
                parts.append(f"- **{r.name}**: {r.description}")
            parts.append("")

        parts.append("### 建议")
        parts.append("1. 严格执行新增规则，特别是止损和仓位控制")
        parts.append("2. 下次交易前检查情绪周期判断")
        parts.append("3. 持续记录交易日志，提高复盘质量")

        return "\n".join(parts)


# 扩展 ReviewRequest 模型以支持完整复盘
class FullReviewRequest:
    """完整复盘请求"""
    review_data: ReviewInput
    tree_id: Optional[str] = "default"
    generate_summary: bool = True
    auto_apply: bool = True  # 是否自动应用生成的规则
