"""Tests for the strategy-mining artifact API."""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.strategy_mining.api import router


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TRADING_STRATEGIES_DIR", str(tmp_path))
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _write_artifacts(directory, artifact_id: str) -> None:
    config = {
        "universe": "csi300",
        "period": "2020-2025",
        "top_n": 20,
        "train_years": 3,
        "max_per_theme": 2,
        "use_market_filter": False,
        "neutralize": True,
        "neutralize_fields": ["sector"],
        "replacement_buffer": 0.12,
        "selected_alphas": ["alpha101_001"],
        "hypothesis_id": "hyp_test",
        "created_at": "2026-06-29T00:00:00+00:00",
    }
    report = {
        "universe": "csi300",
        "period": "2020-2025",
        "metrics": {
            "annual_return_pct": 30.0,
            "sharpe": 1.1,
            "max_drawdown_pct": 25.0,
            "information_ratio": 0.3,
            "turnover_approx": 1.5,
        },
        "n_rebalances": 10,
        "portfolios": {"2020-01-03": ["000001.SZ"]},
    }
    search = {"best_params": config, "best_score": 1.0, "scores": []}
    race = {"metric": "sharpe", "best_name": "test", "scores": []}

    (directory / f"strategy_{artifact_id}.json").write_text(json.dumps(config), encoding="utf-8")
    (directory / f"strategy_report_{artifact_id}.json").write_text(json.dumps(report), encoding="utf-8")
    (directory / f"strategy_search_{artifact_id}.json").write_text(json.dumps(search), encoding="utf-8")
    (directory / f"strategy_race_{artifact_id}.json").write_text(json.dumps(race), encoding="utf-8")


def test_list_strategy_artifacts(client, tmp_path):
    _write_artifacts(tmp_path, "20260629T000000Z")
    resp = client.get("/ashare/strategy-mining/list")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    item = data[0]
    assert item["id"] == "20260629T000000Z"
    assert item["metrics"]["sharpe"] == 1.1
    assert item["selected_alphas"] == ["alpha101_001"]
    assert item["has_report"]
    assert item["has_search"]
    assert item["has_race"]


def test_get_artifact(client, tmp_path):
    _write_artifacts(tmp_path, "20260629T000000Z")
    resp = client.get("/ashare/strategy-mining/artifact/20260629T000000Z?kind=report")
    assert resp.status_code == 200
    assert resp.json()["metrics"]["annual_return_pct"] == 30.0


def test_get_artifact_not_found(client):
    resp = client.get("/ashare/strategy-mining/artifact/missing?kind=report")
    assert resp.status_code == 404


def test_get_artifact_invalid_kind(client, tmp_path):
    _write_artifacts(tmp_path, "20260629T000000Z")
    resp = client.get("/ashare/strategy-mining/artifact/20260629T000000Z?kind=invalid")
    assert resp.status_code == 404
