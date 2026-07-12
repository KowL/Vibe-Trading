"""Tests for A-share mandate and trading tools."""

from src.ashare.trading import AShareMandateConfig, create_default_ashare_mandate
from src.ashare.trading.mandate_tool import AShareMandateTool, AShareTradeTool


class TestAShareMandateConfig:
    def test_default_creation(self):
        config = create_default_ashare_mandate()
        assert config.broker == "simulated"
        assert config.max_order_notional_cny == 100_000.0
        assert config.exclude_st is True

    def test_custom_creation(self):
        config = AShareMandateConfig(
            broker="simulated",
            account_ref="test_001",
            max_order_notional_cny=50_000.0,
            max_trades_per_day=5,
        )
        assert config.max_order_notional_cny == 50_000.0
        assert config.max_trades_per_day == 5

    def test_to_dict(self):
        config = create_default_ashare_mandate()
        data = config.to_dict()
        assert data["broker"] == "simulated"
        assert data["exclude_st"] is True


class TestAShareMandateTool:
    def test_create_mandate(self):
        tool = AShareMandateTool()
        result = tool.execute(action="create", broker="simulated", max_order_cny=50000)
        import json

        data = json.loads(result)
        assert data["status"] == "success"
        assert "mandate_id" in data

    def test_list_mandates(self):
        tool = AShareMandateTool()
        # First create one
        tool.execute(action="create", broker="simulated")
        # Then list
        result = tool.execute(action="list")
        import json

        data = json.loads(result)
        assert data["status"] == "success"
        assert data["count"] >= 1

    def test_revoke_mandate(self):
        tool = AShareMandateTool()
        # Create
        result = tool.execute(action="create", broker="simulated")
        import json

        mid = json.loads(result)["mandate_id"]
        # Revoke
        result = tool.execute(action="revoke", mandate_id=mid)
        data = json.loads(result)
        assert data["status"] == "success"


class TestAShareTradeTool:
    def test_trade_with_valid_mandate(self):
        # Create mandate first
        mandate_tool = AShareMandateTool()
        result = mandate_tool.execute(action="create", broker="simulated", max_order_cny=100000)
        import json

        mid = json.loads(result)["mandate_id"]

        # Execute trade
        trade_tool = AShareTradeTool()
        result = trade_tool.execute(
            mandate_id=mid,
            symbol="600403.SH",
            side="buy",
            quantity=100,
            price=8.14,
        )
        data = json.loads(result)
        assert data["status"] == "success"
        assert data["symbol"] == "600403.SH"
        assert data["simulated"] is True

    def test_trade_exceeds_limit(self):
        # Create mandate with small limit
        mandate_tool = AShareMandateTool()
        result = mandate_tool.execute(action="create", max_order_cny=100)
        import json

        mid = json.loads(result)["mandate_id"]

        # Try to trade over limit
        trade_tool = AShareTradeTool()
        result = trade_tool.execute(
            mandate_id=mid,
            symbol="600403.SH",
            side="buy",
            quantity=1000,
            price=10.0,  # 10,000 CNY > 100 limit
        )
        data = json.loads(result)
        assert data["status"] == "error"
        assert "exceeds" in data["error"]

    def test_trade_without_mandate(self):
        trade_tool = AShareTradeTool()
        result = trade_tool.execute(
            mandate_id="nonexistent",
            symbol="600403.SH",
            side="buy",
            quantity=100,
            price=8.14,
        )
        import json

        data = json.loads(result)
        assert data["status"] == "error"
        assert "not found" in data["error"]
