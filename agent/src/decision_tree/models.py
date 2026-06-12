"""决策树模型定义"""
from enum import Enum
from typing import List, Dict, Optional, Any
from pydantic import BaseModel, Field, field_validator
from datetime import datetime


class SentimentCycle(str, Enum):
    """情绪周期状态"""
    ICE = "冰点"              # 极度恐慌，30%仓试错
    TOP = "高潮"              # 一致性过强，50%仓减仓
    STRUCTURAL_MAIN = "结构主升"  # 主线爆发但连板高度未打开，70%仓聚焦主线
    MAIN = "主升"             # 情绪高涨，80%仓干龙头
    MAIN_LATE = "主升末期"     # 连板高度打开但炸板率上升，70%仓谨慎
    TRIAL = "试错"            # 情绪修复初期，50%仓
    CHAOS_BULL = "混沌偏多"    # 偏多头震荡，60%仓
    CHAOS_BEAR = "混沌偏空"    # 偏空头震荡，40%仓
    CHAOS = "混沌"            # 无法判断，50%仓等待
    RETREAT_EARLY = "退潮初期"  # 高位开始分歧，40%仓减仓
    RETREAT = "退潮"          # 高位瓦解，30%仓清仓


class ActionType(str, Enum):
    """决策动作类型"""
    BUY = "买入"
    SELL = "卖出"
    HOLD = "持有"
    WATCH = "观望"
    REDUCE = "减仓"
    ADD = "加仓"
    STOP_LOSS = "止损"
    TAKE_PROFIT = "止盈"


class RuleCondition(BaseModel):
    """规则条件"""
    field: str = Field(..., description="条件字段，如 'limit_up_count', 'sentiment_cycle'")
    operator: str = Field(..., description="操作符: eq, neq, gt, lt, gte, lte, in, not_in, contains, between, exists")
    value: Any = Field(..., description="条件值")
    description: Optional[str] = Field(None, description="条件说明")

    def evaluate(self, context: Dict[str, Any]) -> bool:
        """评估条件是否满足"""
        actual = context.get(self.field)
        if self.operator == "exists":
            return bool(self.value) is (actual is not None)
        if actual is None:
            return False

        actual_value = actual.value if isinstance(actual, Enum) else actual
        expected_value = self.value.value if isinstance(self.value, Enum) else self.value

        if self.operator == "eq":
            return actual_value == expected_value
        elif self.operator == "neq":
            return actual_value != expected_value
        elif self.operator == "gt":
            return actual_value > expected_value
        elif self.operator == "lt":
            return actual_value < expected_value
        elif self.operator == "gte":
            return actual_value >= expected_value
        elif self.operator == "lte":
            return actual_value <= expected_value
        elif self.operator == "in":
            return actual_value in expected_value if isinstance(expected_value, list) else False
        elif self.operator == "not_in":
            return actual_value not in expected_value if isinstance(expected_value, list) else False
        elif self.operator == "contains":
            if isinstance(actual_value, str):
                return str(expected_value) in actual_value
            if isinstance(actual_value, list):
                return expected_value in actual_value
            return False
        elif self.operator == "between":
            if not isinstance(expected_value, list) or len(expected_value) != 2:
                return False
            low, high = expected_value
            return low <= actual_value <= high
        return False


class DecisionRule(BaseModel):
    """决策规则"""
    id: str = Field(..., description="规则ID")
    name: str = Field(..., description="规则名称")
    description: Optional[str] = Field(None, description="规则描述")
    conditions: List[RuleCondition] = Field(default_factory=list, description="规则条件列表")
    action: ActionType = Field(..., description="执行动作")
    position_pct: Optional[float] = Field(None, description="建议仓位百分比")
    priority: int = Field(100, description="优先级，数字越小优先级越高")
    enabled: bool = Field(True, description="是否启用")
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    created_by: Optional[str] = Field(None, description="创建者：'ai' 或 'human'")
    version: int = Field(1, description="规则版本")
    source: Optional[str] = Field(None, description="规则来源，如 '复盘生成'")

    def evaluate(self, context: Dict[str, Any]) -> bool:
        """评估规则是否满足所有条件"""
        if not self.enabled:
            return False
        return all(c.evaluate(context) for c in self.conditions)


class DecisionTreeNode(BaseModel):
    """决策树节点（用于可视化）"""
    id: str = Field(..., description="节点ID")
    type: str = Field(..., description="节点类型: root, condition, action, branch")
    label: str = Field(..., description="节点标签")
    description: Optional[str] = Field(None, description="节点描述")
    children: List["DecisionTreeNode"] = Field(default_factory=list, description="子节点")
    rule_id: Optional[str] = Field(None, description="关联规则ID")
    position: Optional[Dict[str, float]] = Field(None, description="可视化位置 {x, y}")
    style: Optional[Dict[str, Any]] = Field(None, description="可视化样式")


class DecisionTree(BaseModel):
    """决策树定义"""
    id: str = Field(..., description="决策树ID")
    name: str = Field(..., description="决策树名称")
    description: Optional[str] = Field(None, description="决策树描述")
    version: int = Field(1, description="版本")
    rules: List[DecisionRule] = Field(default_factory=list, description="规则列表")
    root_node: Optional[DecisionTreeNode] = Field(None, description="根节点（可视化用）")
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    active: bool = Field(True, description="是否激活")

    @field_validator("rules")
    @classmethod
    def sort_rules(cls, rules: List[DecisionRule]) -> List[DecisionRule]:
        return sorted(rules, key=lambda r: (r.priority, r.created_at))

    def evaluate(self, context: Dict[str, Any]) -> Optional[DecisionRule]:
        """评估决策树，返回最高优先级匹配的规则"""
        matching_rules = [r for r in self.rules if r.evaluate(context)]
        if not matching_rules:
            return None
        return min(matching_rules, key=lambda r: r.priority)


class ReviewInput(BaseModel):
    """复盘输入数据"""
    date: str = Field(..., description="复盘日期 YYYY-MM-DD")
    market_summary: str = Field(..., description="市场总结")
    trades: List[Dict[str, Any]] = Field(default_factory=list, description="交易记录")
    market_data: Dict[str, Any] = Field(default_factory=dict, description="市场数据")
    sentiment_cycle: Optional[str] = Field(None, description="情绪周期")
    mistakes: List[str] = Field(default_factory=list, description="错误总结")
    lessons: List[str] = Field(default_factory=list, description="经验教训")


class ReviewOutput(BaseModel):
    """复盘输出 - 生成的决策树更新"""
    review_id: str = Field(..., description="复盘ID")
    date: str = Field(..., description="复盘日期")
    new_rules: List[DecisionRule] = Field(default_factory=list, description="新增规则")
    modified_rules: List[DecisionRule] = Field(default_factory=list, description="修改规则")
    removed_rule_ids: List[str] = Field(default_factory=list, description="移除规则ID")
    summary: str = Field(..., description="复盘总结")
    confidence: float = Field(0.8, description="置信度 0-1")


class DecisionTreeVisualization(BaseModel):
    """决策树可视化数据"""
    nodes: List[Dict[str, Any]] = Field(default_factory=list, description="节点列表")
    edges: List[Dict[str, Any]] = Field(default_factory=list, description="边列表")
    layout: str = Field("tree", description="布局类型: tree, radial, flow")
