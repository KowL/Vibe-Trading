"""A-share (中国 A 股) extensions for Vibe-Trading.

Ports key concepts from Ruo.ai:
* LimitUpDaily 涨停数据模型
* Portfolio / Trade 持仓管理
* Market-report tasks (开盘/收盘/周度)
* AmazingData-first data source with tushare/akshare/mootdx fallbacks
"""

from __future__ import annotations
