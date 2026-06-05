"""Tests for A-share data models."""

from datetime import date

from src.ashare.models.limit_up import LimitUpDaily
from src.ashare.models.portfolio import Portfolio, Trade, TradeSide, TradeStatus


class TestLimitUpDaily:
    def test_creation(self):
        record = LimitUpDaily(
            trade_date=date(2026, 6, 5),
            symbol="600403.SH",
            name="大有能源",
            limit_up_price=8.14,
            limit_up_count=5,
        )
        assert record.symbol == "600403.SH"
        assert record.limit_up_count == 5

    def test_symbol_suffix(self):
        """Test that symbols have correct exchange suffix."""
        sh = LimitUpDaily(trade_date=date.today(), symbol="600403.SH", name="")
        sz = LimitUpDaily(trade_date=date.today(), symbol="000001.SZ", name="")
        assert sh.symbol.endswith(".SH")
        assert sz.symbol.endswith(".SZ")

    def test_sealed_inference(self):
        """Test sealed status inference from prices."""
        sealed = LimitUpDaily(
            trade_date=date.today(),
            symbol="600403.SH",
            name="",
            limit_up_price=8.14,
            close_price=8.14,
        )
        assert sealed.is_sealed is True

        broken = LimitUpDaily(
            trade_date=date.today(),
            symbol="600403.SH",
            name="",
            limit_up_price=8.14,
            close_price=7.50,
        )
        assert broken.is_sealed is False

    def test_to_dict_roundtrip(self):
        """Test serialization round-trip."""
        original = LimitUpDaily(
            trade_date=date(2026, 6, 5),
            symbol="600403.SH",
            name="大有能源",
            limit_up_count=3,
        )
        data = original.to_dict()
        restored = LimitUpDaily.from_dict(data)
        assert restored.symbol == original.symbol
        assert restored.limit_up_count == original.limit_up_count


class TestPortfolio:
    def test_creation(self):
        pf = Portfolio(
            portfolio_id="test_pf",
            name="测试账户",
            initial_cash=100000.0,
            cash=100000.0,
        )
        assert pf.cash == 100000.0
        assert pf.total_value == 100000.0

    def test_trade_creation(self):
        trade = Trade(
            trade_id="t1",
            portfolio_id="test",
            symbol="600403.SH",
            side=TradeSide.BUY,
            quantity=100,
            price=8.14,
        )
        assert trade.amount == 814.0
        assert trade.status == TradeStatus.OPEN

    def test_trade_close(self):
        """Test PnL calculation for a round-trip trade."""
        trade = Trade(
            trade_id="t1",
            portfolio_id="test",
            symbol="600403.SH",
            side=TradeSide.BUY,
            quantity=100,
            price=8.0,
        )
        trade.close(close_price=9.0)
        assert trade.status == TradeStatus.CLOSED
        assert trade.pnl == 100.0
        assert trade.close_price == 9.0

    def test_portfolio_metrics(self):
        """Test portfolio return calculation."""
        pf = Portfolio(
            portfolio_id="test",
            name="",
            initial_cash=100000.0,
            cash=90000.0,
            market_value=15000.0,
        )
        pf.update_metrics()
        assert pf.total_value == 105000.0
        assert pf.total_return_pct == 5.0
