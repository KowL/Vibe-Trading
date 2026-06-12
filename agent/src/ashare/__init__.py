"""A-share (中国 A 股) extensions for Vibe-Trading.

Ports key concepts from Ruo.ai:
* LimitUpDaily 涨停数据模型
* Portfolio / Trade 持仓管理
* Market-report tasks (开盘/收盘/周度)
* akshare-first data source (东方财富免费接口) with adshare/tushare fallbacks
"""

from __future__ import annotations
