"""Tests for the stock-name resolver fallback chain."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from src.ashare.strategies.stock_names import (
    _FALLBACK_NAMES,
    _load_from_codes_parquet,
    _load_from_env_file,
    _load_name_map,
    _MIN_VALID_NAMES,
    get_stock_name,
    reset_stock_name_cache,
)


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    reset_stock_name_cache()
    yield
    reset_stock_name_cache()


def _many_names(n: int = _MIN_VALID_NAMES) -> list[dict[str, str]]:
    return [
        {"code": f"{i:06d}.SZ", "name": f"股票{i}"}
        for i in range(1, n + 1)
    ]


def test_get_stock_name_uses_fallback_when_tushare_returns_empty_names() -> None:
    """Regression: empty-name tushare responses must not poison the cache."""
    bad_resp = {
        "data": [
            {"code": "000001.SZ", "name": ""},
            {"code": "000002.SZ", "name": ""},
        ]
    }

    with patch("src.ashare.tushare_client.TushareClient") as MockClient:
        MockClient.return_value.get_stock_basic.return_value = bad_resp
        # Isolate from any real codes.parquet or env file on the host.
        with patch(
            "src.ashare.strategies.stock_names._load_from_codes_parquet",
            return_value=None,
        ):
            with patch(
                "src.ashare.strategies.stock_names._load_from_env_file",
                return_value=None,
            ):
                assert get_stock_name("000001.SZ") == "平安银行"
                assert get_stock_name("000002.SZ") == "万科A"
                assert get_stock_name("UNKNOWN.XY") == ""


def test_get_stock_name_uses_tushare_when_names_are_valid() -> None:
    resp = {"data": _many_names() + [{"code": "999999.XY", "name": "测试股票"}]}

    with patch("src.ashare.tushare_client.TushareClient") as MockClient:
        MockClient.return_value.get_stock_basic.return_value = resp
        assert get_stock_name("000001.SZ") == "股票1"
        assert get_stock_name("999999.XY") == "测试股票"


def test_load_name_map_requires_threshold_of_non_empty_names() -> None:
    """A sparse/empty response should be rejected and fall back."""
    bad_resp = {"data": [{"code": "000001.SZ", "name": ""} for _ in range(1500)]}

    with patch("src.ashare.tushare_client.TushareClient") as MockClient:
        MockClient.return_value.get_stock_basic.return_value = bad_resp
        with patch(
            "src.ashare.strategies.stock_names._load_from_codes_parquet",
            return_value=None,
        ):
            with patch(
                "src.ashare.strategies.stock_names._load_from_env_file",
                return_value=None,
            ):
                name_map = _load_name_map()
                assert name_map == _FALLBACK_NAMES


def test_load_name_map_accepts_large_valid_response() -> None:
    data = _many_names(1200)

    with patch("src.ashare.tushare_client.TushareClient") as MockClient:
        MockClient.return_value.get_stock_basic.return_value = {"data": data}
        name_map = _load_name_map()
        assert len(name_map) == 1200
        assert name_map["000001.SZ"] == "股票1"


def test_load_from_env_file_json(tmp_path: Path) -> None:
    path = tmp_path / "names.json"
    names_dict = {
        f"{i:06d}.SZ": f"股票{i}"
        for i in range(1, _MIN_VALID_NAMES + 1)
    }
    names_dict["000001.SZ"] = "平安银行"
    path.write_text(json.dumps(names_dict, ensure_ascii=False), encoding="utf-8")

    with patch.dict(os.environ, {"STOCK_NAMES_PATH": str(path)}):
        names = _load_from_env_file()

    assert names is not None
    assert names.get("000001.SZ") == "平安银行"


def test_load_from_env_file_csv(tmp_path: Path) -> None:
    path = tmp_path / "names.csv"
    lines = ["code,name"]
    lines.extend(f"{i:06d}.SZ,股票{i}" for i in range(1, _MIN_VALID_NAMES + 1))
    path.write_text("\n".join(lines), encoding="utf-8")

    with patch.dict(os.environ, {"STOCK_NAMES_PATH": str(path)}):
        names = _load_from_env_file()

    assert names is not None
    assert names.get("000001.SZ") == "股票1"
    assert len(names) == _MIN_VALID_NAMES


def test_load_name_map_falls_back_to_env_file() -> None:
    """When tushare returns empty names, an env file with enough names is used."""
    bad_resp = {"data": [{"code": "000001.SZ", "name": ""}]}
    env_names = {
        f"{i:06d}.SZ": f"股票{i}"
        for i in range(1, _MIN_VALID_NAMES + 1)
    }
    env_names["000001.SZ"] = "平安银行"

    with patch("src.ashare.tushare_client.TushareClient") as MockClient:
        MockClient.return_value.get_stock_basic.return_value = bad_resp
        with patch(
            "src.ashare.strategies.stock_names._load_from_codes_parquet",
            return_value=None,
        ):
            with patch(
                "src.ashare.strategies.stock_names._load_from_env_file",
                return_value=env_names,
            ):
                name_map = _load_name_map()
                assert name_map.get("000001.SZ") == "平安银行"
                assert len(name_map) == _MIN_VALID_NAMES


def test_load_from_codes_parquet_returns_none_when_missing(tmp_path: Path) -> None:
    """If no codes.parquet exists in the detected data root, return None."""
    with patch(
        "src.ashare.strategies.stock_names._detect_data_root",
        return_value=tmp_path,
    ):
        assert _load_from_codes_parquet() is None
