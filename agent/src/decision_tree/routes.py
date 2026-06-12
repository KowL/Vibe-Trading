"""决策树 API 路由"""
import json
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .models import (
    DecisionTree, DecisionRule, ActionType,
    RuleCondition, ReviewInput, DecisionTreeVisualization
)
from .engine import DecisionTreeEngine, SentimentAnalyzer
from .ai_review import AIReviewEngine

router = APIRouter(prefix="/decision-tree", tags=["decision-tree"])

# 存储路径
STORAGE_DIR = Path(__file__).parent / "storage"
STORAGE_DIR.mkdir(exist_ok=True)
DEFAULT_TREE_ID = "default_tree"
_TREE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# 内存缓存
tree_cache: Dict[str, DecisionTreeEngine] = {}


def _normalize_tree_id(tree_id: str) -> str:
    normalized = DEFAULT_TREE_ID if tree_id in {"default", DEFAULT_TREE_ID} else tree_id
    if not _TREE_ID_RE.fullmatch(normalized):
        raise HTTPException(status_code=400, detail="非法决策树ID")
    return normalized


def _get_tree_path(tree_id: str) -> Path:
    return STORAGE_DIR / f"{_normalize_tree_id(tree_id)}.json"


def _save_tree(tree: DecisionTree) -> None:
    path = _get_tree_path(tree.id)
    data = tree.model_dump(mode="json")
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _load_tree(tree_id: str) -> Optional[DecisionTree]:
    path = _get_tree_path(tree_id)
    if not path.exists():
        return None
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return DecisionTree(**data)


def _ensure_default_tree() -> DecisionTree:
    tree = _load_tree(DEFAULT_TREE_ID)
    if tree:
        return tree
    engine = DecisionTreeEngine()
    _save_tree(engine.tree)
    tree_cache[engine.tree.id] = engine
    return engine.tree


def _get_engine(tree_id: str = DEFAULT_TREE_ID) -> DecisionTreeEngine:
    tree_id = _normalize_tree_id(tree_id)
    if tree_id in tree_cache:
        return tree_cache[tree_id]

    tree = _load_tree(tree_id)
    if tree:
        engine = DecisionTreeEngine(tree)
    elif tree_id == DEFAULT_TREE_ID:
        engine = DecisionTreeEngine()
        _save_tree(engine.tree)
    else:
        raise HTTPException(status_code=404, detail="决策树不存在")

    tree_cache[tree_id] = engine
    return engine


def _parse_action(action: str) -> ActionType:
    if action in ActionType.__members__:
        return ActionType[action]
    try:
        return ActionType(action)
    except ValueError as exc:
        allowed = [item.value for item in ActionType]
        raise HTTPException(status_code=422, detail=f"无效动作，允许值: {', '.join(allowed)}") from exc


def _parse_conditions(raw_conditions: List[Dict[str, Any]]) -> List[RuleCondition]:
    return [RuleCondition(**condition) for condition in raw_conditions]


# ======== Pydantic 请求/响应模型 ========

class CreateTreeRequest(BaseModel):
    name: str = Field(..., description="决策树名称")
    description: Optional[str] = Field(None, description="描述")


class UpdateTreeRequest(BaseModel):
    name: Optional[str] = Field(None, description="决策树名称")
    description: Optional[str] = Field(None, description="描述")
    active: Optional[bool] = Field(None, description="是否激活")


class CreateRuleRequest(BaseModel):
    name: str = Field(..., description="规则名称")
    description: Optional[str] = Field(None, description="规则描述")
    conditions: List[Dict] = Field(default_factory=list, description="条件列表")
    action: str = Field(..., description="动作: 买入, 卖出, 持有, 观望, 减仓, 加仓, 止损, 止盈")
    position_pct: Optional[float] = Field(None, description="仓位百分比")
    priority: int = Field(100, description="优先级")
    enabled: bool = Field(True, description="是否启用")


class UpdateRuleRequest(BaseModel):
    name: Optional[str] = Field(None, description="规则名称")
    description: Optional[str] = Field(None, description="规则描述")
    conditions: Optional[List[Dict]] = Field(None, description="条件列表")
    action: Optional[str] = Field(None, description="动作")
    position_pct: Optional[float] = Field(None, description="仓位百分比")
    priority: Optional[int] = Field(None, description="优先级")
    enabled: Optional[bool] = Field(None, description="是否启用")


class EvaluateRequest(BaseModel):
    context: Dict = Field(..., description="评估上下文")


class ReviewRequest(BaseModel):
    review_data: ReviewInput = Field(..., description="复盘输入数据")
    tree_id: Optional[str] = Field("default", description="决策树ID")


class SentimentRequest(BaseModel):
    limit_up_count: int = Field(..., description="涨停数量")
    limit_down_count: int = Field(..., description="跌停数量")
    max_limit_up_streak: int = Field(..., description="最高连板数")
    broken_board_rate: float = Field(..., description="炸板率")
    up_down_ratio: float = Field(..., description="涨跌比")
    prev_limit_up_premium: float = Field(0.0, description="昨日涨停溢价")


# ======== API 端点 ========

@router.get("/list")
async def list_trees() -> List[Dict]:
    """列出所有决策树"""
    _ensure_default_tree()
    trees = []
    for path in STORAGE_DIR.glob("*.json"):
        tree = _load_tree(path.stem)
        if tree:
            trees.append({
                "id": tree.id,
                "name": tree.name,
                "description": tree.description,
                "version": tree.version,
                "active": tree.active,
                "rule_count": len(tree.rules),
                "created_at": tree.created_at.isoformat() if hasattr(tree.created_at, 'isoformat') else str(tree.created_at),
                "updated_at": tree.updated_at.isoformat() if hasattr(tree.updated_at, 'isoformat') else str(tree.updated_at),
            })
    return sorted(trees, key=lambda t: (t["id"] != DEFAULT_TREE_ID, t["created_at"]))


@router.post("/create")
async def create_tree(req: CreateTreeRequest) -> Dict:
    """创建新决策树"""
    if not req.name.strip():
        raise HTTPException(status_code=422, detail="决策树名称不能为空")
    tree_id = f"tree_{uuid.uuid4().hex[:8]}"
    tree = DecisionTree(
        id=tree_id,
        name=req.name.strip(),
        description=req.description
    )
    _save_tree(tree)
    tree_cache[tree_id] = DecisionTreeEngine(tree)
    return {"id": tree_id, "name": tree.name, "message": "决策树创建成功"}


@router.get("/{tree_id}")
async def get_tree(tree_id: str) -> Dict:
    """获取决策树详情"""
    tree = _load_tree(tree_id)
    if not tree:
        raise HTTPException(status_code=404, detail="决策树不存在")
    return tree.model_dump(mode="json")


@router.patch("/{tree_id}")
async def update_tree(tree_id: str, req: UpdateTreeRequest) -> Dict:
    """更新决策树元信息"""
    engine = _get_engine(tree_id)
    if req.name is not None:
        if not req.name.strip():
            raise HTTPException(status_code=422, detail="决策树名称不能为空")
        engine.tree.name = req.name.strip()
    if req.description is not None:
        engine.tree.description = req.description
    if req.active is not None:
        engine.tree.active = req.active
    engine.tree.version += 1
    engine.tree.updated_at = datetime.now()
    _save_tree(engine.tree)
    return {"message": "决策树更新成功", "tree": engine.tree.model_dump(mode="json")}


@router.delete("/{tree_id}")
async def delete_tree(tree_id: str) -> Dict:
    """删除决策树"""
    tree_id = _normalize_tree_id(tree_id)
    if tree_id == DEFAULT_TREE_ID:
        raise HTTPException(status_code=400, detail="默认决策树不能删除")
    path = _get_tree_path(tree_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="决策树不存在")
    path.unlink()
    tree_cache.pop(tree_id, None)
    return {"status": "ok", "message": "决策树删除成功"}


@router.get("/{tree_id}/visualize")
async def visualize_tree(tree_id: str) -> DecisionTreeVisualization:
    """获取决策树可视化数据"""
    engine = _get_engine(tree_id)
    return engine.to_visualization()


@router.post("/{tree_id}/rules")
async def add_rule(tree_id: str, req: CreateRuleRequest) -> Dict:
    """添加规则"""
    engine = _get_engine(tree_id)

    if not req.name.strip():
        raise HTTPException(status_code=422, detail="规则名称不能为空")

    conditions = _parse_conditions(req.conditions)

    rule = DecisionRule(
        id=f"rule_{uuid.uuid4().hex[:8]}",
        name=req.name.strip(),
        description=req.description,
        conditions=conditions,
        action=_parse_action(req.action),
        position_pct=req.position_pct,
        priority=req.priority,
        enabled=req.enabled,
        created_by="human"
    )

    engine.add_rule(rule)
    _save_tree(engine.tree)

    return {"rule_id": rule.id, "rule": rule.model_dump(mode="json"), "message": "规则添加成功"}


@router.patch("/{tree_id}/rules/{rule_id}")
async def update_rule(tree_id: str, rule_id: str, req: UpdateRuleRequest) -> Dict:
    """更新规则"""
    engine = _get_engine(tree_id)
    current = next((rule for rule in engine.tree.rules if rule.id == rule_id), None)
    if not current:
        raise HTTPException(status_code=404, detail="规则不存在")

    updates = req.model_dump(exclude_unset=True)
    if "name" in updates:
        if not str(updates["name"]).strip():
            raise HTTPException(status_code=422, detail="规则名称不能为空")
        updates["name"] = str(updates["name"]).strip()
    if "conditions" in updates and updates["conditions"] is not None:
        updates["conditions"] = _parse_conditions(updates["conditions"])
    if "action" in updates and updates["action"] is not None:
        updates["action"] = _parse_action(updates["action"])

    updated = current.model_copy(update=updates)
    # Force validation for nested condition/action updates before mutating the tree.
    DecisionRule.model_validate(updated.model_dump())
    success = engine.update_rule(rule_id, updates)
    if not success:
        raise HTTPException(status_code=404, detail="规则不存在")
    _save_tree(engine.tree)
    saved = next(rule for rule in engine.tree.rules if rule.id == rule_id)
    return {"rule": saved.model_dump(mode="json"), "message": "规则更新成功"}


@router.patch("/{tree_id}/rules/{rule_id}/toggle")
async def toggle_rule(tree_id: str, rule_id: str) -> Dict:
    """启用或停用规则"""
    engine = _get_engine(tree_id)
    rule = next((item for item in engine.tree.rules if item.id == rule_id), None)
    if not rule:
        raise HTTPException(status_code=404, detail="规则不存在")
    engine.update_rule(rule_id, {"enabled": not rule.enabled})
    _save_tree(engine.tree)
    updated = next(item for item in engine.tree.rules if item.id == rule_id)
    return {"rule": updated.model_dump(mode="json"), "message": "规则状态已更新"}


@router.delete("/{tree_id}/rules/{rule_id}")
async def remove_rule(tree_id: str, rule_id: str) -> Dict:
    """删除规则"""
    engine = _get_engine(tree_id)
    success = engine.remove_rule(rule_id)
    if not success:
        raise HTTPException(status_code=404, detail="规则不存在")
    _save_tree(engine.tree)
    return {"message": "规则删除成功"}


@router.post("/{tree_id}/evaluate")
async def evaluate_tree(tree_id: str, req: EvaluateRequest) -> Dict:
    """评估决策树"""
    engine = _get_engine(tree_id)
    context = dict(req.context)
    rule = engine.evaluate(context)

    if rule:
        return {
            "matched": True,
            "rule": rule.model_dump(mode="json"),
            "context": context,
            "recommendation": {
                "action": rule.action.value,
                "position_pct": rule.position_pct,
                "message": f"触发规则: {rule.name} - 建议{rule.action.value}，仓位{rule.position_pct}%"
            }
        }

    return {
        "matched": False,
        "context": context,
        "recommendation": {
            "action": ActionType.WATCH.value,
            "position_pct": 50.0,
            "message": "未触发任何规则，建议观望"
        }
    }


@router.post("/sentiment")
async def analyze_sentiment(req: SentimentRequest) -> Dict:
    """分析情绪周期"""
    analyzer = SentimentAnalyzer()
    cycle, details = analyzer.analyze(
        limit_up_count=req.limit_up_count,
        limit_down_count=req.limit_down_count,
        max_limit_up_streak=req.max_limit_up_streak,
        broken_board_rate=req.broken_board_rate,
        up_down_ratio=req.up_down_ratio,
        prev_limit_up_premium=req.prev_limit_up_premium
    )

    return {
        "sentiment_cycle": cycle.value if hasattr(cycle, "value") else str(cycle),
        "details": details,
        "description": f"当前情绪周期: {cycle.value if hasattr(cycle, 'value') else str(cycle)}"
    }


@router.post("/{tree_id}/review")
async def review_tree(tree_id: str, req: ReviewRequest) -> Dict:
    """
    AI 复盘 - 根据交易复盘数据生成决策树更新建议
    """
    engine = _get_engine(tree_id)

    ai_engine = AIReviewEngine(engine)
    result = ai_engine.review(req.review_data)

    _save_tree(engine.tree)

    return {
        "review_id": result.review_id,
        "date": result.date,
        "new_rules_count": len(result.new_rules),
        "modified_rules_count": len(result.modified_rules),
        "removed_rules_count": len(result.removed_rule_ids),
        "new_rules": [r.model_dump(mode="json") for r in result.new_rules],
        "modified_rules": [r.model_dump(mode="json") for r in result.modified_rules],
        "summary": result.summary,
        "confidence": result.confidence
    }
