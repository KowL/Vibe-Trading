# A-Share (A股) 模块

Vibe-Trading 的 A 股短线交易模块，从 Ruo.ai 迁移而来。

## 功能概览

| 功能 | 状态 | 说明 |
|------|------|------|
| 涨停梯队 | ✅ | 每日涨停股票池，含连板高度、封单金额 |
| 模拟持仓 | ✅ | 虚拟账户管理，支持买卖记录 |
| 市场报告 | ✅ | 开盘/收盘/周度复盘报告 |
| 定时任务 | ✅ | 自动同步数据、生成报告 |
| 实时推送 | ✅ | SSE 事件流，任务完成自动通知 |
| 回测引擎 | ✅ | 连板策略回测，胜率/收益/回撤 |
| 实盘对接 | ✅ | Mandate 授权书 + 模拟交易 |

## 架构

```
agent/src/ashare/
├── api/routes.py          # REST API (FastAPI)
├── backtest/              # 回测引擎
│   └── limit_up_backtest.py
├── cli_handlers.py        # CLI 命令处理
├── live_publisher.py      # SSE 实时推送
├── models/                # 数据模型
│   ├── limit_up.py
│   ├── portfolio.py
│   └── market_report.py
├── scheduler.py           # 定时任务调度器
├── storage/               # 文件系统存储
│   ├── limit_up_store.py
│   ├── portfolio_store.py
│   └── report_store.py
├── tasks/                 # 定时任务
│   ├── limit_up_sync.py
│   ├── market_report.py
│   └── portfolio_sync.py
├── trading/               # 实盘交易
│   ├── __init__.py        # Mandate 配置
│   └── mandate_tool.py    # 交易工具
└── README.md              # 本文档
```

## 快速开始

### CLI 命令

```bash
# 查看今日涨停
vibe-trading ashare limit-up

# 同步指定日期数据
vibe-trading ashare limit-up --date 2026-06-05 --sync

# 创建模拟账户
vibe-trading ashare portfolio --create --name "测试账户" --cash 100000

# 生成收盘报告
vibe-trading ashare report --kind close --generate
```

### API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/ashare/limit-up/{date}` | 获取涨停数据 |
| POST | `/ashare/limit-up/sync` | 同步涨停数据 |
| GET | `/ashare/portfolios` | 列出模拟账户 |
| POST | `/ashare/portfolios` | 创建模拟账户 |
| GET | `/ashare/reports/{kind}/{date}` | 获取报告 |
| POST | `/ashare/backtest/limit-up` | 运行回测 |
| GET | `/ashare/events` | SSE 实时事件 |

### Agent 工具

| 工具名 | 功能 |
|--------|------|
| `ashare_limit_up` | 获取涨停梯队 |
| `ashare_sync_limit_up` | 同步涨停数据 |
| `ashare_portfolio` | 管理模拟持仓 |
| `ashare_report` | 获取市场报告 |
| `ashare_backtest` | 运行策略回测 |
| `ashare_mandate` | 管理交易授权 |
| `ashare_trade` | 执行交易 |

## 数据源

- **adshare** (localhost:8000)
- 端点: `/market/limit-up`
- 字段映射: `limitUpDays` → `limit_up_count`, `price` → `limit_up_price`

## 配置

环境变量 (`.env`):
```
ADSHARE_URL=http://localhost:8000
```

## 定时任务

| 任务 | 时间 | 说明 |
|------|------|------|
| limit_up_sync | 15:30 | 收盘后同步涨停数据 |
| market_report_open | 09:00 | 开盘前生成报告 |
| market_report_close | 18:00 | 收盘后生成复盘 |
| weekly_report | 周五 19:00 | 周度复盘 |

## 测试

```bash
# 运行所有测试
pytest agent/tests/ashare/ -v

# 运行特定测试
pytest agent/tests/ashare/test_models.py -v
```

## 注意事项

1. **实盘交易**: 当前仅支持模拟交易（broker=simulated）
2. **Mandate 授权**: 所有实盘交易必须通过授权书检查
3. **ST 排除**: 默认排除 ST/*ST 股票
4. **数据延迟**: adshare 数据可能有 15 分钟延迟
