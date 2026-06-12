import React, { useState, useEffect } from "react";
const API_BASE = "";

interface BacktestResult {
  start_date: string;
  end_date: string;
  total_return_pct: number;
  annualized_return_pct: number;
  max_drawdown_pct: number;
  sharpe_ratio: number;
  win_rate: number;
  profit_factor: number;
  num_trades: number;
  avg_holding_days: number;
}

interface StockPick {
  symbol: string;
  composite_score: number;
  momentum_20d: number;
  volume_ratio: number;
  ma5: number;
  ma20: number;
  ma60: number;
}

export default function StrategyPage() {
  const [activeTab, setActiveTab] = useState<"select" | "backtest" | "profile">("select");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<any>(null);
  const [error, setError] = useState("");

  // Selection params
  const [tradeDate, setTradeDate] = useState(new Date().toISOString().split("T")[0]);
  const [topN, setTopN] = useState(20);

  // Backtest params
  const [startDate, setStartDate] = useState("2025-01-01");
  const [endDate, setEndDate] = useState("2025-06-10");
  const [initialCash, setInitialCash] = useState(1000000);

  // Profile params
  const [profileSymbol, setProfileSymbol] = useState("000001.SZ");

  const runSelection = async () => {
    setLoading(true);
    setError("");
    try {
      const resp = await fetch(
        `${API_BASE}/ashare/strategy/select?trade_date=${tradeDate}&top_n=${topN}`
      );
      const data = await resp.json();
      setResult({ type: "select", data });
    } catch (e) {
      setError(String(e));
    }
    setLoading(false);
  };

  const runBacktest = async () => {
    setLoading(true);
    setError("");
    try {
      const resp = await fetch(`${API_BASE}/ashare/strategy/backtest`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          start_date: startDate,
          end_date: endDate,
          initial_cash: initialCash,
        }),
      });
      const data = await resp.json();
      setResult({ type: "backtest", data });
    } catch (e) {
      setError(String(e));
    }
    setLoading(false);
  };

  const runProfile = async () => {
    setLoading(true);
    setError("");
    try {
      const resp = await fetch(
        `${API_BASE}/ashare/strategy/profile?symbol=${profileSymbol}`
      );
      const data = await resp.json();
      setResult({ type: "profile", data });
    } catch (e) {
      setError(String(e));
    }
    setLoading(false);
  };

  return (
    <div className="p-6 max-w-7xl mx-auto">
      <h1 className="text-2xl font-bold mb-6">Multi-Factor Strategy</h1>

      {/* Tabs */}
      <div className="flex gap-2 mb-6">
        <button
          onClick={() => setActiveTab("select")}
          className={`px-4 py-2 rounded ${
            activeTab === "select" ? "bg-blue-600 text-white" : "bg-gray-200"
          }`}
        >
          Stock Selection
        </button>
        <button
          onClick={() => setActiveTab("backtest")}
          className={`px-4 py-2 rounded ${
            activeTab === "backtest" ? "bg-blue-600 text-white" : "bg-gray-200"
          }`}
        >
          Backtest
        </button>
        <button
          onClick={() => setActiveTab("profile")}
          className={`px-4 py-2 rounded ${
            activeTab === "profile" ? "bg-blue-600 text-white" : "bg-gray-200"
          }`}
        >
          Stock Profile
        </button>
      </div>

      {/* Selection Panel */}
      {activeTab === "select" && (
        <div className="bg-white p-4 rounded-lg shadow">
          <h2 className="text-lg font-semibold mb-4">Multi-Factor Stock Selection</h2>
          <div className="flex gap-4 mb-4">
            <div>
              <label className="block text-sm text-gray-600">Trade Date</label>
              <input
                type="date"
                value={tradeDate}
                onChange={(e: React.ChangeEvent<HTMLInputElement>) => setTradeDate(e.target.value)}
                className="border rounded px-2 py-1"
              />
            </div>
            <div>
              <label className="block text-sm text-gray-600">Top N</label>
              <input
                type="number"
                value={topN}
                onChange={(e: React.ChangeEvent<HTMLInputElement>) => setTopN(Number(e.target.value))}
                className="border rounded px-2 py-1 w-20"
              />
            </div>
            <button
              onClick={runSelection}
              disabled={loading}
              className="bg-blue-600 text-white px-4 py-2 rounded hover:bg-blue-700 disabled:opacity-50"
            >
              {loading ? "Running..." : "Run Selection"}
            </button>
          </div>

          {result?.type === "select" && (
            <div className="mt-4">
              <p className="text-sm text-gray-600 mb-2">
                Selected {result.data.selected_count} stocks for {result.data.trade_date}
              </p>
              <table className="w-full text-sm">
                <thead className="bg-gray-100">
                  <tr>
                    <th className="px-2 py-1 text-left">Symbol</th>
                    <th className="px-2 py-1 text-right">Score</th>
                    <th className="px-2 py-1 text-right">Momentum</th>
                    <th className="px-2 py-1 text-right">Vol Ratio</th>
                    <th className="px-2 py-1 text-right">MA5</th>
                    <th className="px-2 py-1 text-right">MA20</th>
                    <th className="px-2 py-1 text-right">MA60</th>
                  </tr>
                </thead>
                <tbody>
                  {result.data.stocks.map((s: StockPick) => (
                    <tr key={s.symbol} className="border-b">
                      <td className="px-2 py-1 font-mono">{s.symbol}</td>
                      <td className="px-2 py-1 text-right">{s.composite_score.toFixed(3)}</td>
                      <td className="px-2 py-1 text-right">{s.momentum_20d.toFixed(1)}%</td>
                      <td className="px-2 py-1 text-right">{s.volume_ratio.toFixed(2)}x</td>
                      <td className="px-2 py-1 text-right">{s.ma5.toFixed(2)}</td>
                      <td className="px-2 py-1 text-right">{s.ma20.toFixed(2)}</td>
                      <td className="px-2 py-1 text-right">{s.ma60.toFixed(2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* Backtest Panel */}
      {activeTab === "backtest" && (
        <div className="bg-white p-4 rounded-lg shadow">
          <h2 className="text-lg font-semibold mb-4">Strategy Backtest</h2>
          <div className="flex gap-4 mb-4">
            <div>
              <label className="block text-sm text-gray-600">Start Date</label>
              <input
                type="date"
                value={startDate}
                onChange={(e: React.ChangeEvent<HTMLInputElement>) => setStartDate(e.target.value)}
                className="border rounded px-2 py-1"
              />
            </div>
            <div>
              <label className="block text-sm text-gray-600">End Date</label>
              <input
                type="date"
                value={endDate}
                onChange={(e: React.ChangeEvent<HTMLInputElement>) => setEndDate(e.target.value)}
                className="border rounded px-2 py-1"
              />
            </div>
            <div>
              <label className="block text-sm text-gray-600">Initial Cash</label>
              <input
                type="number"
                value={initialCash}
                onChange={(e: React.ChangeEvent<HTMLInputElement>) => setInitialCash(Number(e.target.value))}
                className="border rounded px-2 py-1 w-32"
              />
            </div>
            <button
              onClick={runBacktest}
              disabled={loading}
              className="bg-blue-600 text-white px-4 py-2 rounded hover:bg-blue-700 disabled:opacity-50"
            >
              {loading ? "Running..." : "Run Backtest"}
            </button>
          </div>

          {result?.type === "backtest" && (
            <div className="mt-4">
              <div className="grid grid-cols-4 gap-4 mb-4">
                <div className="bg-green-50 p-3 rounded">
                  <div className="text-sm text-gray-600">Total Return</div>
                  <div className="text-xl font-bold text-green-600">
                    {result.data.total_return_pct}%
                  </div>
                </div>
                <div className="bg-blue-50 p-3 rounded">
                  <div className="text-sm text-gray-600">Sharpe Ratio</div>
                  <div className="text-xl font-bold text-blue-600">
                    {result.data.sharpe_ratio}
                  </div>
                </div>
                <div className="bg-red-50 p-3 rounded">
                  <div className="text-sm text-gray-600">Max Drawdown</div>
                  <div className="text-xl font-bold text-red-600">
                    {result.data.max_drawdown_pct}%
                  </div>
                </div>
                <div className="bg-purple-50 p-3 rounded">
                  <div className="text-sm text-gray-600">Win Rate</div>
                  <div className="text-xl font-bold text-purple-600">
                    {result.data.win_rate}%
                  </div>
                </div>
              </div>
              <div className="grid grid-cols-4 gap-4 mb-4">
                <div className="bg-gray-50 p-3 rounded">
                  <div className="text-sm text-gray-600">Annualized</div>
                  <div className="text-lg font-semibold">{result.data.annualized_return_pct}%</div>
                </div>
                <div className="bg-gray-50 p-3 rounded">
                  <div className="text-sm text-gray-600">Profit Factor</div>
                  <div className="text-lg font-semibold">{result.data.profit_factor}</div>
                </div>
                <div className="bg-gray-50 p-3 rounded">
                  <div className="text-sm text-gray-600">Trades</div>
                  <div className="text-lg font-semibold">{result.data.num_trades}</div>
                </div>
                <div className="bg-gray-50 p-3 rounded">
                  <div className="text-sm text-gray-600">Avg Hold</div>
                  <div className="text-lg font-semibold">{result.data.avg_holding_days}d</div>
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Profile Panel */}
      {activeTab === "profile" && (
        <div className="bg-white p-4 rounded-lg shadow">
          <h2 className="text-lg font-semibold mb-4">Stock Personality Profile</h2>
          <div className="flex gap-4 mb-4">
            <div>
              <label className="block text-sm text-gray-600">Symbol</label>
              <input
                type="text"
                value={profileSymbol}
                onChange={(e: React.ChangeEvent<HTMLInputElement>) => setProfileSymbol(e.target.value)}
                className="border rounded px-2 py-1 w-32"
              />
            </div>
            <button
              onClick={runProfile}
              disabled={loading}
              className="bg-blue-600 text-white px-4 py-2 rounded hover:bg-blue-700 disabled:opacity-50"
            >
              {loading ? "Analyzing..." : "Analyze"}
            </button>
          </div>

          {result?.type === "profile" && result.data.profile && (
            <div className="mt-4 grid grid-cols-2 gap-4">
              <div className="bg-gray-50 p-4 rounded">
                <h3 className="font-semibold mb-2">Profile</h3>
                <div className="space-y-1 text-sm">
                  <div>Personality: <span className="font-semibold">{result.data.profile.personality}</span></div>
                  <div>Risk Level: <span className="font-semibold">{result.data.profile.risk_level}</span></div>
                  <div>HV20: {result.data.profile.hv_20}%</div>
                  <div>ATR: {result.data.profile.atr_pct}%</div>
                  <div>ADX: {result.data.profile.adx_14}</div>
                  <div>Trend: {result.data.profile.trend_direction}</div>
                  <div>Hurst: {result.data.profile.hurst_exponent}</div>
                </div>
              </div>
              <div className="bg-blue-50 p-4 rounded">
                <h3 className="font-semibold mb-2">Adaptive Parameters</h3>
                <div className="space-y-1 text-sm">
                  <div>Stop Loss: {result.data.adaptive_params.stop_loss_pct * 100}%</div>
                  <div>Take Profit: {result.data.adaptive_params.take_profit_pct * 100}%</div>
                  <div>Max Position: {result.data.adaptive_params.max_position_pct * 100}%</div>
                  <div>Risk/Trade: {result.data.adaptive_params.risk_per_trade_pct * 100}%</div>
                  <div>Max Hold: {result.data.adaptive_params.max_holding_days} days</div>
                  <div>Min Momentum: {result.data.adaptive_params.min_momentum_pct}%</div>
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {error && (
        <div className="mt-4 p-4 bg-red-50 text-red-700 rounded">{error}</div>
      )}
    </div>
  );
}

export default StrategyPage;
