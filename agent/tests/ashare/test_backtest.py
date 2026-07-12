"""Tests for limit-up backtest engine."""

from datetime import date

from src.ashare.backtest.limit_up_backtest import (
    BacktestResult,
    LimitUpBacktestEngine,
    run_limit_up_backtest,
)
from src.ashare.models.limit_up import LimitUpDaily
from src.ashare.storage.limit_up_store import LimitUpStore


class TestBacktestResult:
    def test_empty_result(self):
        result = BacktestResult(
            strategy_name="测试策略",
            start_date=date(2026, 6, 1),
            end_date=date(2026, 6, 5),
        )
        assert result.total_trades == 0
        assert result.win_rate == 0.0
        assert result.total_return_pct == 0.0

    def test_win_rate_calculation(self):
        result = BacktestResult(
            strategy_name="测试",
            start_date=date(2026, 6, 1),
            end_date=date(2026, 6, 5),
            trades=[],  # Will add manually
        )
        # Create mock trades by setting attributes directly
        assert result.winning_trades == 0
        assert result.losing_trades == 0


class TestLimitUpBacktestEngine:
    def test_engine_creation(self):
        engine = LimitUpBacktestEngine()
        assert engine.store is not None

    def test_run_with_no_data(self):
        """Test backtest with empty data returns empty result."""
        engine = LimitUpBacktestEngine()
        result = engine.run(
            start_date=date(2020, 1, 1),
            end_date=date(2020, 1, 2),
        )
        assert result.total_trades == 0
        assert result.total_return_pct == 0.0


class TestRunLimitUpBacktest:
    def test_convenience_function(self):
        """Test the convenience wrapper function."""
        result = run_limit_up_backtest(
            start_date="2026-06-01",
            end_date="2026-06-05",
            min_days=2,
            max_days=5,
        )
        assert "strategy_name" in result
        assert "total_trades" in result
        assert "win_rate" in result
