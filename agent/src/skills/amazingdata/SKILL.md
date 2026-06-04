---
name: amazingdata
category: data-source
description: AmazingData local A-share data service (localhost:3100). Real-time quotes, K-line, stock list, limit-up data. Primary data source for Ruo.ai A-share trading.
---

## Overview

AmazingData is a local A-share data service running at `localhost:3100`. It provides real-time market data for Chinese stocks including quotes, K-lines, stock lists, and limit-up/limit-down data.

- Endpoint: `http://localhost:3100`
- No API key required (local service)
- Response format: JSON

## Quick Start

```python
import requests

BASE = "http://localhost:3100"

# Stock list
r = requests.get(f"{BASE}/stock/list")
stocks = r.json()  # [{code, name, market, ...}, ...]

# Real-time quote
r = requests.get(f"{BASE}/stock/quote", params={"code": "000001"})
quote = r.json()  # {code, name, price, open, high, low, prev_close, volume, ...}

# K-line data
r = requests.get(f"{BASE}/stock/kline", params={"code": "000001", "period": "day", "count": 60})
kline = r.json()  # [{date, open, high, low, close, volume}, ...]
```

## API Endpoints

### Stock List

| Endpoint | Method | Params | Description |
|----------|--------|--------|-------------|
| `/stock/list` | GET | - | All A-share stocks |
| `/stock/list` | GET | `market=SH` | Shanghai stocks only |
| `/stock/list` | GET | `market=SZ` | Shenzhen stocks only |

Response:
```json
[
  {"code": "000001", "name": "平安银行", "market": "SZ"},
  {"code": "600000", "name": "浦发银行", "market": "SH"}
]
```

### Real-time Quote

| Endpoint | Method | Params | Description |
|----------|--------|--------|-------------|
| `/stock/quote` | GET | `code` (required) | Single stock quote |

Response:
```json
{
  "code": "000001",
  "name": "平安银行",
  "price": 10.52,
  "open": 10.30,
  "high": 10.60,
  "low": 10.25,
  "prev_close": 10.28,
  "volume": 1250000,
  "amount": 13150000,
  "pct_change": 2.33,
  "bid1": 10.51,
  "ask1": 10.52,
  "bid_vol1": 500,
  "ask_vol1": 300
}
```

### K-line Data

| Endpoint | Method | Params | Description |
|----------|--------|--------|-------------|
| `/stock/kline` | GET | `code`, `period`, `count` | Historical OHLCV |

Params:
- `code`: Stock code (e.g. "000001")
- `period`: `day` / `week` / `month` / `1min` / `5min` / `15min` / `30min` / `60min`
- `count`: Number of bars (max 1000)
- `start_date`: Optional, format YYYYMMDD
- `end_date`: Optional, format YYYYMMDD

Response:
```json
[
  {"date": "20250601", "open": 10.20, "high": 10.35, "low": 10.15, "close": 10.28, "volume": 980000},
  {"date": "20250602", "open": 10.28, "high": 10.40, "low": 10.22, "close": 10.35, "volume": 1100000}
]
```

### Limit-up / Limit-down

| Endpoint | Method | Params | Description |
|----------|--------|--------|-------------|
| `/stock/limit-up` | GET | `date` (optional) | Limit-up stocks |
| `/stock/limit-down` | GET | `date` (optional) | Limit-down stocks |

Response:
```json
[
  {
    "code": "000001",
    "name": "平安银行",
    "limit_price": 11.31,
    "first_time": "09:35:00",
    "last_time": "14:55:00",
    "open_count": 2,
    "volume": 5200000
  }
]
```

### Limit-up Statistics (连板数据)

| Endpoint | Method | Params | Description |
|----------|--------|--------|-------------|
| `/stock/limit-up-stats` | GET | `date` (optional) | Limit-up statistics |

Response:
```json
[
  {
    "code": "000001",
    "name": "平安银行",
    "consecutive_days": 3,
    "limit_price": 11.31,
    "volume_ratio": 1.85
  }
]
```

## Symbol Format

- A-shares: pure digits (e.g. `"000001"`, `"600000"`)
- No `.SH` / `.SZ` suffix required for most endpoints
- The service auto-detects market based on code prefix

## Error Handling

All endpoints return HTTP 200 on success with JSON body. On error:

```json
{"error": "Stock not found", "code": "000001"}
```

## Health Check

```python
import requests
r = requests.get("http://localhost:3100/health")
print(r.json())  # {"status": "ok", "version": "1.0.0"}
```

## Integration Notes

- This is a **local service** — must be running before use
- Default port: 3100 (configurable via `AMAZINGDATA_PORT` env var)
- No rate limiting (local loopback)
- Response time: < 10ms typical
- Data latency: Real-time (WebSocket push) or < 1s (HTTP poll)

## Fallback Chain

When AmazingData is unavailable, the runner falls back to:
1. `tushare` (if token configured)
2. `akshare` (free, no key)
3. `mootdx` (local TDX data)
