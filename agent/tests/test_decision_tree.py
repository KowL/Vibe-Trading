from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.decision_tree import routes


def _client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setattr(routes, "STORAGE_DIR", tmp_path)
    routes.tree_cache.clear()
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app)


def test_list_trees_creates_complete_default_tree(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    response = client.get("/decision-tree/list")

    assert response.status_code == 200
    trees = response.json()
    assert trees[0]["id"] == routes.DEFAULT_TREE_ID
    assert trees[0]["rule_count"] == 11
    assert (tmp_path / "default_tree.json").exists()


def test_evaluate_auto_analyzes_sentiment_cycle(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    client.get("/decision-tree/list")

    response = client.post(
        "/decision-tree/default_tree/evaluate",
        json={
            "context": {
                "market_data": {
                    "limit_up_count": 8,
                    "limit_down_count": 60,
                    "max_limit_up_streak": 2,
                    "broken_board_rate": 0.55,
                    "up_down_ratio": 0.4,
                }
            }
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["matched"] is True
    assert body["context"]["sentiment_cycle"] == "冰点"
    assert body["recommendation"]["action"] == "买入"
    assert body["recommendation"]["position_pct"] == 30.0


def test_rule_crud_and_custom_tree_delete(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    created = client.post("/decision-tree/create", json={"name": "短线纪律"}).json()
    tree_id = created["id"]

    added = client.post(
        f"/decision-tree/{tree_id}/rules",
        json={
            "name": "大亏止损",
            "conditions": [{"field": "hold_loss_pct", "operator": "lte", "value": -5}],
            "action": "止损",
            "position_pct": 0,
            "priority": 1,
        },
    )
    assert added.status_code == 200
    rule_id = added.json()["rule_id"]

    matched = client.post(
        f"/decision-tree/{tree_id}/evaluate",
        json={"context": {"hold_loss_pct": -6}},
    ).json()
    assert matched["recommendation"]["action"] == "止损"

    patched = client.patch(
        f"/decision-tree/{tree_id}/rules/{rule_id}",
        json={"enabled": False, "priority": 5},
    )
    assert patched.status_code == 200
    assert patched.json()["rule"]["enabled"] is False

    unmatched = client.post(
        f"/decision-tree/{tree_id}/evaluate",
        json={"context": {"hold_loss_pct": -6}},
    ).json()
    assert unmatched["matched"] is False

    toggled = client.patch(f"/decision-tree/{tree_id}/rules/{rule_id}/toggle")
    assert toggled.status_code == 200
    assert toggled.json()["rule"]["enabled"] is True

    deleted = client.delete(f"/decision-tree/{tree_id}")
    assert deleted.status_code == 200
    assert not (tmp_path / f"{tree_id}.json").exists()


def test_default_tree_cannot_be_deleted(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    client.get("/decision-tree/list")

    response = client.delete("/decision-tree/default_tree")

    assert response.status_code == 400
