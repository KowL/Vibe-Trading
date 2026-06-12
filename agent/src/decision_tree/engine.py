"""决策树引擎 - 核心规则执行和情绪周期判断"""
import json
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime
from pathlib import Path

from .models import (
    DecisionTree, DecisionRule,
    SentimentCycle, ActionType, RuleCondition, DecisionTreeVisualization
)


class SentimentAnalyzer:
    """情绪周期分析器"""

    @staticmethod
    def analyze(
        limit_up_count: int,
        limit_down_count: int,
        max_limit_up_streak: int,
        broken_board_rate: float,
        up_down_ratio: float,
        prev_limit_up_premium: float = 0.0
    ) -> Tuple[str, Dict[str, Any]]:
        """
        分析市场情绪周期

        Returns:
            (情绪周期名称, 详细数据)
        """
        details = {
            "limit_up_count": limit_up_count,
            "limit_down_count": limit_down_count,
            "max_limit_up_streak": max_limit_up_streak,
            "broken_board_rate": broken_board_rate,
            "up_down_ratio": up_down_ratio,
            "prev_limit_up_premium": prev_limit_up_premium
        }

        # 极端判断
        if limit_up_count <= 10 and limit_down_count >= 50:
            return SentimentCycle.ICE, details

        if limit_up_count >= 100 and limit_down_count <= 5 and broken_board_rate < 0.1:
            return SentimentCycle.TOP, details

        # 主升阶段判断
        if max_limit_up_streak >= 5 and limit_up_count >= 60:
            if broken_board_rate > 0.3:
                return SentimentCycle.MAIN_LATE, details
            return SentimentCycle.MAIN, details

        if max_limit_up_streak >= 3 and limit_up_count >= 40:
            return SentimentCycle.STRUCTURAL_MAIN, details

        # 退潮阶段判断
        if max_limit_up_streak <= 3 and limit_up_count < 40 and broken_board_rate > 0.4:
            if limit_down_count > 20:
                return SentimentCycle.RETREAT, details
            return SentimentCycle.RETREAT_EARLY, details

        # 试错/混沌
        if limit_up_count >= 30 and limit_down_count < 15:
            if up_down_ratio > 1.5:
                return SentimentCycle.CHAOS_BULL, details
            return SentimentCycle.TRIAL, details

        if up_down_ratio < 0.8:
            return SentimentCycle.CHAOS_BEAR, details

        return SentimentCycle.CHAOS, details


class DecisionTreeEngine:
    """决策树引擎"""

    def __init__(self, tree: Optional[DecisionTree] = None):
        self.tree = tree or self._create_default_tree()
        self.sentiment_analyzer = SentimentAnalyzer()

    def _create_default_tree(self) -> DecisionTree:
        """创建默认决策树"""
        rules = [
            DecisionRule(
                id="rule_ice_001",
                name="冰点试错",
                description="极度恐慌时，30%仓位试错",
                conditions=[
                    RuleCondition(field="sentiment_cycle", operator="eq", value=SentimentCycle.ICE),
                ],
                action=ActionType.BUY,
                position_pct=30.0,
                priority=10,
                created_by="system"
            ),
            DecisionRule(
                id="rule_structural_main_001",
                name="结构主升聚焦主线",
                description="主线爆发但连板高度未完全打开，70%仓位聚焦主线核心",
                conditions=[
                    RuleCondition(field="sentiment_cycle", operator="eq", value=SentimentCycle.STRUCTURAL_MAIN),
                ],
                action=ActionType.ADD,
                position_pct=70.0,
                priority=10,
                created_by="system"
            ),
            DecisionRule(
                id="rule_main_001",
                name="主升干龙头",
                description="情绪高涨，80%仓位聚焦龙头",
                conditions=[
                    RuleCondition(field="sentiment_cycle", operator="eq", value=SentimentCycle.MAIN),
                ],
                action=ActionType.BUY,
                position_pct=80.0,
                priority=10,
                created_by="system"
            ),
            DecisionRule(
                id="rule_main_late_001",
                name="主升末期谨慎",
                description="连板高度打开但炸板率上升，70%仓位",
                conditions=[
                    RuleCondition(field="sentiment_cycle", operator="eq", value=SentimentCycle.MAIN_LATE),
                ],
                action=ActionType.HOLD,
                position_pct=70.0,
                priority=10,
                created_by="system"
            ),
            DecisionRule(
                id="rule_trial_001",
                name="试错期轻仓验证",
                description="情绪修复初期，50%仓位试错，等待主线确认",
                conditions=[
                    RuleCondition(field="sentiment_cycle", operator="eq", value=SentimentCycle.TRIAL),
                ],
                action=ActionType.BUY,
                position_pct=50.0,
                priority=15,
                created_by="system"
            ),
            DecisionRule(
                id="rule_chaos_bull_001",
                name="混沌偏多控仓参与",
                description="偏多震荡时保留弹性，60%仓位参与强势方向",
                conditions=[
                    RuleCondition(field="sentiment_cycle", operator="eq", value=SentimentCycle.CHAOS_BULL),
                ],
                action=ActionType.HOLD,
                position_pct=60.0,
                priority=15,
                created_by="system"
            ),
            DecisionRule(
                id="rule_chaos_bear_001",
                name="混沌偏空防守",
                description="偏空震荡时降低暴露，40%仓位以内观察",
                conditions=[
                    RuleCondition(field="sentiment_cycle", operator="eq", value=SentimentCycle.CHAOS_BEAR),
                ],
                action=ActionType.WATCH,
                position_pct=40.0,
                priority=15,
                created_by="system"
            ),
            DecisionRule(
                id="rule_retreat_early_001",
                name="退潮初期减仓",
                description="高位开始分歧，40%仓位防守，停止追高",
                conditions=[
                    RuleCondition(field="sentiment_cycle", operator="eq", value=SentimentCycle.RETREAT_EARLY),
                ],
                action=ActionType.REDUCE,
                position_pct=40.0,
                priority=10,
                created_by="system"
            ),
            DecisionRule(
                id="rule_retreat_001",
                name="退潮清仓",
                description="高位瓦解，30%仓位清仓",
                conditions=[
                    RuleCondition(field="sentiment_cycle", operator="eq", value=SentimentCycle.RETREAT),
                ],
                action=ActionType.SELL,
                position_pct=30.0,
                priority=10,
                created_by="system"
            ),
            DecisionRule(
                id="rule_chaos_001",
                name="混沌等待",
                description="无法判断，50%仓位等待",
                conditions=[
                    RuleCondition(field="sentiment_cycle", operator="eq", value=SentimentCycle.CHAOS),
                ],
                action=ActionType.WATCH,
                position_pct=50.0,
                priority=20,
                created_by="system"
            ),
            DecisionRule(
                id="rule_top_001",
                name="高潮减仓",
                description="一致性过强，50%仓位减仓",
                conditions=[
                    RuleCondition(field="sentiment_cycle", operator="eq", value=SentimentCycle.TOP),
                ],
                action=ActionType.REDUCE,
                position_pct=50.0,
                priority=10,
                created_by="system"
            ),
        ]

        return DecisionTree(
            id="default_tree",
            name="默认交易决策树",
            description="基于情绪周期的基础交易决策树",
            rules=rules,
        )

    def evaluate(self, context: Dict[str, Any]) -> Optional[DecisionRule]:
        """评估决策树"""
        if "sentiment_cycle" not in context and "market_data" in context:
            # 自动分析情绪周期
            md = context["market_data"]
            cycle, details = self.sentiment_analyzer.analyze(
                limit_up_count=md.get("limit_up_count", 0),
                limit_down_count=md.get("limit_down_count", 0),
                max_limit_up_streak=md.get("max_limit_up_streak", 0),
                broken_board_rate=md.get("broken_board_rate", 0.0),
                up_down_ratio=md.get("up_down_ratio", 1.0),
                prev_limit_up_premium=md.get("prev_limit_up_premium", 0.0)
            )
            context["sentiment_cycle"] = cycle
            context["sentiment_details"] = details

        return self.tree.evaluate(context)

    def add_rule(self, rule: DecisionRule) -> None:
        """添加规则"""
        self.tree.rules.append(rule)
        self.tree.rules = sorted(self.tree.rules, key=lambda r: (r.priority, r.created_at))
        self.tree.version += 1
        self.tree.updated_at = datetime.now()

    def remove_rule(self, rule_id: str) -> bool:
        """移除规则"""
        original_len = len(self.tree.rules)
        self.tree.rules = [r for r in self.tree.rules if r.id != rule_id]
        if len(self.tree.rules) < original_len:
            self.tree.version += 1
            self.tree.updated_at = datetime.now()
            return True
        return False

    def update_rule(self, rule_id: str, updates: Dict[str, Any]) -> bool:
        """更新规则"""
        for index, rule in enumerate(self.tree.rules):
            if rule.id == rule_id:
                data = rule.model_dump()
                for key, value in updates.items():
                    if key in data:
                        data[key] = value
                data["updated_at"] = datetime.now()
                data["version"] = rule.version + 1
                self.tree.rules[index] = DecisionRule.model_validate(data)
                self.tree.rules = sorted(self.tree.rules, key=lambda r: (r.priority, r.created_at))
                self.tree.version += 1
                self.tree.updated_at = datetime.now()
                return True
        return False

    def to_visualization(self) -> DecisionTreeVisualization:
        """生成可视化数据"""
        nodes = []
        edges = []

        # 根节点
        root_id = f"node_{self.tree.id}_root"
        nodes.append({
            "id": root_id,
            "type": "root",
            "label": self.tree.name,
            "description": self.tree.description,
            "style": {"color": "#4F46E5", "shape": "circle", "size": 40}
        })

        # 按情绪周期分组，未绑定情绪周期的规则进入通用规则组。
        sentiment_groups: Dict[str, List[DecisionRule]] = {}
        for rule in self.tree.rules:
            cycle = "通用规则"
            for cond in rule.conditions:
                if cond.field == "sentiment_cycle":
                    cycle = cond.value.value if hasattr(cond.value, "value") else str(cond.value)
                    break
            sentiment_groups.setdefault(cycle, []).append(rule)

        # 创建情绪周期节点
        y_offset = 0
        for cycle, rules in sentiment_groups.items():
            cycle_id = f"node_cycle_{cycle}"
            nodes.append({
                "id": cycle_id,
                "type": "condition",
                "label": cycle,
                "description": f"{len(rules)} 条规则",
                "position": {"x": 200, "y": y_offset},
                "style": {"color": "#10B981", "shape": "diamond", "size": 30}
            })
            edges.append({
                "source": root_id,
                "target": cycle_id,
                "label": ""
            })

            # 动作节点
            for i, rule in enumerate(rules):
                action_id = f"node_action_{rule.id}"
                position = "--" if rule.position_pct is None else f"{rule.position_pct:g}%"
                color = "#6B7280"
                if not rule.enabled:
                    color = "#9CA3AF"
                elif rule.action in (ActionType.BUY, ActionType.ADD):
                    color = "#10B981"
                elif rule.action in (ActionType.SELL, ActionType.REDUCE, ActionType.STOP_LOSS):
                    color = "#EF4444"
                elif rule.action == ActionType.TAKE_PROFIT:
                    color = "#F59E0B"
                nodes.append({
                    "id": action_id,
                    "type": "action",
                    "label": f"{rule.action.value} ({position})",
                    "description": rule.description or rule.name,
                    "rule_id": rule.id,
                    "enabled": rule.enabled,
                    "priority": rule.priority,
                    "position": {"x": 400, "y": y_offset + i * 60},
                    "style": {
                        "color": color,
                        "shape": "rect",
                        "size": 25
                    }
                })
                edges.append({
                    "source": cycle_id,
                    "target": action_id,
                    "label": rule.name
                })

            y_offset += max(len(rules) * 60, 80)

        return DecisionTreeVisualization(
            nodes=nodes,
            edges=edges,
            layout="tree"
        )

    def save(self, path: Path) -> None:
        """保存决策树到文件"""
        data = self.tree.model_dump(mode="json")
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, path: Path) -> "DecisionTreeEngine":
        """从文件加载决策树"""
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        tree = DecisionTree(**data)
        return cls(tree)
