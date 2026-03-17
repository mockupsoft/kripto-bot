'use client';

import { useEffect, useState } from 'react';
import { apiFetch } from '@/lib/api';
import { fmtUsd, fmtPct, fmtNum } from '@/lib/format';
import type { EquityPoint } from '@/lib/types';

interface Metrics {
  total_trades: number;
  win_rate: number;
  gross_pnl: number;
  net_pnl: number;
  total_fees: number;
  total_slippage: number;
  avg_win: number;
  avg_loss: number;
  profit_factor: number;
  max_drawdown: number;
  expectancy: number;
  expectancy_pct: number;
  avg_trade_pnl: number;
}

interface PerStrategy {
  strategy: string;
  trade_count: number;
  total_pnl: number;
  win_rate: number;
  avg_win?: number;
  avg_loss?: number;
  profit_factor?: number | null;
  expectancy?: number;
  expectancy_pct?: number;
  max_drawdown?: number;
  gross_profit?: number;
  gross_loss?: number;
  verdict?: string;
  pnl_share_pct?: number;
}

interface PerWallet {
  wallet_id: string;
  trade_count: number;
  total_pnl: number;
  win_rate: number;
}

interface ExitBreakdownRow {
  reason: string;
  count: number;
  total_pnl: number;
  wins: number;
  losses: number;
  avg_pnl: number;
  win_rate: number;
}

interface ExitBreakdown {
  total_closed: number;
  open_count: number;
  breakdown: ExitBreakdownRow[];
}


interface DecaySweepPoint {
  latency_ms: number;
  avg_edge_remaining: number;
  max_edge_remaining: number;
  pct_wallets_positive: number;
}

interface PrecisionWindow {
  profitable: number;
  total_closed: number;
  precision: number | null;
  avg_pnl: number | null;
}

interface PrecisionData {
  grade: string;
  interpretation: string;
  windows: { '24h': PrecisionWindow; '7d': PrecisionWindow; all_time: PrecisionWindow };
}

interface StrategyHealthRolling {
  window: number;
  trade_count: number;
  win_rate: number;
  profit_factor: number | null;
  expectancy: number;
  gross_profit: number;
  gross_loss: number;
}

interface StrategyHealthEntry {
  strategy: string;
  mode: 'active' | 'shadow' | 'paused';
  kill_active: boolean;
  kill_reason: string | null;
  capital_share: number;
  rolling: StrategyHealthRolling;
  last_7d: { trade_count: number; profit_factor: number | null; expectancy: number | null };
  trend: 'improving' | 'stable' | 'deteriorating';
  verdict: string;
}

interface StrategyHealth {
  strategies: StrategyHealthEntry[];
  rolling_window: number;
  summary: {
    active_count: number;
    shadow_count: number;
    kill_active_count: number;
    total_capital_allocated: number;
  };
}

// ── Edge Calibration types ────────────────────────────────────────────────────
interface EdgeBucket {
  bucket: string;
  threshold: string;
  n: number;
  avg_pnl: number | null;
}

interface EdgeCalibration {
  generated_at: string;
  sample_size: number;
  overall: { pearson_r: number | null; verdict: string; interpretation: string };
  by_side: {
    buy:  { pearson_r: number | null; verdict: string };
    sell: { pearson_r: number | null; verdict: string };
  };
  by_strategy: {
    direct_copy:     { pearson_r: number | null };
    high_conviction: { pearson_r: number | null };
  };
  edge_buckets: EdgeBucket[];
  high_edge_miss_analysis: {
    high_edge_total: number;
    high_edge_losses: number;
    miss_rate: number | null;
    by_exit_reason: { wallet_reversal: number; stop_loss: number; max_hold_time: number };
    diagnosis: string;
  };
  note: string;
}

// ── Checkpoint types ──────────────────────────────────────────────────────────
interface CheckpointStats {
  label: string;
  window_n: number;
  trade_count: number;
  win_count: number;
  loss_count: number;
  win_rate: number;
  gross_profit: number;
  gross_loss: number;
  profit_factor: number | null;
  expectancy: number;
  total_fees: number;
  total_slippage: number;
  cost_gross_ratio: number | null;
  avg_position_size_usd: number;
  avg_hold_minutes: number | null;
  buy_win_rate: number | null;
  sell_win_rate: number | null;
  buy_count: number;
  sell_count: number;
  exit_breakdown: Record<string, number>;
  strategy_pnl: Record<string, number>;
  strategy_count: Record<string, number>;
  verdict: string;
}

interface CheckpointData {
  generated_at: string;
  summary: {
    total_real_trading_exits: number;
    total_infra_exits: number;
    open_positions: number;
    current_milestone: number | null;
    next_milestone: number | null;
    trades_until_next: number;
  };
  checkpoints: CheckpointStats[];
  all_time: CheckpointStats | null;
}


function MetricCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-white/5 bg-surface-raised p-4">
      <p className="text-xs text-gray-500">{label}</p>
      <p className="mt-1 text-lg font-semibold">{value}</p>
    </div>
  );
}

const EXIT_REASON_META: Record<string, { label: string; color: string; icon: string }> = {
  target_hit:      { label: 'Target Hit',     color: 'text-green-400',  icon: '↑' },
  stop_loss:       { label: 'Stop Loss',       color: 'text-red-400',    icon: '✕' },
  max_hold_time:   { label: 'Time Limit',      color: 'text-yellow-400', icon: '⏱' },
  stale_data:      { label: 'Stale Data',      color: 'text-orange-400', icon: '⚠' },
  market_resolved: { label: 'Market Closed',  color: 'text-blue-400',   icon: '✓' },
  unknown:         { label: 'Unknown',         color: 'text-gray-500',   icon: '?' },
};

function ExitBreakdownPanel({ data }: { data: ExitBreakdown }) {
  const maxCount = Math.max(...data.breakdown.map((r) => r.count), 1);

  return (
    <div className="space-y-3">
      {/* Summary chips */}
      <div className="flex gap-3 text-xs">
        <span className="rounded bg-white/5 px-2 py-1">
          <span className="text-gray-400">Open</span>{' '}
          <span className="font-semibold text-yellow-400">{data.open_count}</span>
        </span>
        <span className="rounded bg-white/5 px-2 py-1">
          <span className="text-gray-400">Closed</span>{' '}
          <span className="font-semibold text-white">{data.total_closed}</span>
        </span>
      </div>

      {/* Breakdown table */}
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-white/5 text-gray-500">
              <th className="py-2 text-left">Exit Reason</th>
              <th className="py-2 text-right">Count</th>
              <th className="py-2 text-right">Win Rate</th>
              <th className="py-2 text-right">Avg PnL</th>
              <th className="py-2 text-right">Total PnL</th>
              <th className="py-2 pl-4 text-left">Volume</th>
            </tr>
          </thead>
          <tbody>
            {data.breakdown.map((row) => {
              const meta = EXIT_REASON_META[row.reason] ?? EXIT_REASON_META.unknown;
              const barPct = (row.count / maxCount) * 100;
              return (
                <tr key={row.reason} className="border-b border-white/5">
                  <td className="py-2 font-medium">
                    <span className={`mr-1.5 ${meta.color}`}>{meta.icon}</span>
                    <span className={row.count > 0 ? 'text-white' : 'text-gray-600'}>{meta.label}</span>
                  </td>
                  <td className="py-2 text-right font-mono">{row.count}</td>
                  <td className={`py-2 text-right font-mono ${row.win_rate >= 0.5 ? 'text-green-400' : row.count > 0 ? 'text-red-400' : 'text-gray-600'}`}>
                    {row.count > 0 ? `${(row.win_rate * 100).toFixed(0)}%` : '—'}
                  </td>
                  <td className={`py-2 text-right font-mono ${row.avg_pnl > 0 ? 'text-green-400' : row.avg_pnl < 0 ? 'text-red-400' : 'text-gray-600'}`}>
                    {row.count > 0 ? (row.avg_pnl >= 0 ? '+' : '') + row.avg_pnl.toFixed(3) : '—'}
                  </td>
                  <td className={`py-2 text-right font-mono ${row.total_pnl > 0 ? 'text-green-400' : row.total_pnl < 0 ? 'text-red-400' : 'text-gray-600'}`}>
                    {row.count > 0 ? (row.total_pnl >= 0 ? '+$' : '-$') + Math.abs(row.total_pnl).toFixed(3) : '—'}
                  </td>
                  <td className="py-2 pl-4">
                    <div className="h-2 w-24 overflow-hidden rounded-full bg-white/5">
                      <div
                        className={`h-full rounded-full transition-all ${row.count > 0 ? (meta.color.replace('text-', 'bg-') + '/60') : 'bg-transparent'}`}
                        style={{ width: `${barPct}%` }}
                      />
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}


function LatencyDecayChart({ sweep }: { sweep: DecaySweepPoint[] }) {
  if (!sweep.length) return <p className="text-sm text-gray-500">No decay data yet. Need scored wallets first.</p>;

  const maxVal = 1.0;
  const barWidth = 100 / sweep.length;

  return (
    <div className="space-y-4">
      <div className="relative h-40">
        {/* Grid lines */}
        {[0, 25, 50, 75, 100].map((pct) => (
          <div
            key={pct}
            className="absolute inset-x-0 border-t border-white/5"
            style={{ bottom: `${pct}%` }}
          >
            <span className="absolute -top-3 right-0 text-[10px] text-gray-600">{pct}%</span>
          </div>
        ))}
        {/* Bars */}
        <div className="absolute inset-0 flex items-end gap-2 px-2">
          {sweep.map((p) => {
            const avgH = (p.avg_edge_remaining / maxVal) * 100;
            const maxH = (p.max_edge_remaining / maxVal) * 100;
            const pctH = p.pct_wallets_positive * 100;
            return (
              <div key={p.latency_ms} className="flex flex-1 flex-col items-center gap-1">
                <div className="relative w-full">
                  {/* Max edge bar (background) */}
                  <div
                    className="absolute inset-x-0 bottom-0 rounded-t bg-blue-500/20"
                    style={{ height: `${maxH * 1.5}px` }}
                  />
                  {/* Avg edge bar */}
                  <div
                    className={`relative rounded-t transition-all ${avgH > 50 ? 'bg-green-500' : avgH > 25 ? 'bg-yellow-500' : 'bg-red-500'}`}
                    style={{ height: `${Math.max(2, avgH * 1.5)}px` }}
                    title={`Avg: ${(p.avg_edge_remaining * 100).toFixed(1)}% | Max: ${(p.max_edge_remaining * 100).toFixed(1)}%`}
                  />
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* X-axis labels */}
      <div className="flex gap-2 px-2">
        {sweep.map((p) => (
          <div key={p.latency_ms} className="flex-1 text-center text-[10px] text-gray-500">
            {p.latency_ms}ms
          </div>
        ))}
      </div>

      {/* Data table */}
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-white/5 text-gray-500">
              <th className="py-1.5 text-left">Latency</th>
              <th className="py-1.5 text-right">Avg Edge</th>
              <th className="py-1.5 text-right">Best Edge</th>
              <th className="py-1.5 text-right">Copyable Wallets</th>
            </tr>
          </thead>
          <tbody>
            {sweep.map((p) => (
              <tr key={p.latency_ms} className="border-b border-white/5">
                <td className="py-1.5 font-mono">{p.latency_ms}ms</td>
                <td className={`py-1.5 text-right font-medium ${p.avg_edge_remaining > 0.3 ? 'text-green-400' : p.avg_edge_remaining > 0.1 ? 'text-yellow-400' : 'text-red-400'}`}>
                  {(p.avg_edge_remaining * 100).toFixed(1)}%
                </td>
                <td className="py-1.5 text-right text-gray-300">{(p.max_edge_remaining * 100).toFixed(1)}%</td>
                <td className="py-1.5 text-right text-gray-300">{(p.pct_wallets_positive * 100).toFixed(0)}%</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function AnalyticsPage() {
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [equity, setEquity] = useState<EquityPoint[]>([]);
  const [perStrategy, setPerStrategy] = useState<PerStrategy[]>([]);
  const [perWallet, setPerWallet] = useState<PerWallet[]>([]);
  const [decaySweep, setDecaySweep] = useState<DecaySweepPoint[]>([]);
  const [exitBreakdown, setExitBreakdown] = useState<ExitBreakdown | null>(null);
  const [precision, setPrecision] = useState<PrecisionData | null>(null);
  const [stratHealth, setStratHealth] = useState<StrategyHealth | null>(null);
  const [checkpoint, setCheckpoint]   = useState<CheckpointData | null>(null);
  const [edgeCalib, setEdgeCalib]     = useState<EdgeCalibration | null>(null);

  useEffect(() => {
    apiFetch<Metrics>('/api/analytics/metrics').then(setMetrics).catch(() => {});
    apiFetch<{ equity_curve: EquityPoint[] }>('/api/analytics/equity-curve').then((d) => setEquity(d.equity_curve)).catch(() => {});
    apiFetch<{ per_strategy: PerStrategy[] }>('/api/analytics/per-strategy').then((d) => setPerStrategy(d.per_strategy)).catch(() => {});
    apiFetch<{ per_wallet: PerWallet[] }>('/api/analytics/per-wallet').then((d) => setPerWallet(d.per_wallet)).catch(() => {});
    apiFetch<{ sweep: DecaySweepPoint[] }>('/api/analytics/latency-decay').then((d) => setDecaySweep(d.sweep || [])).catch(() => {});
    apiFetch<ExitBreakdown>('/api/analytics/exit-breakdown').then(setExitBreakdown).catch(() => {});
    apiFetch<PrecisionData>('/api/markets/actionability-precision').then(setPrecision).catch(() => {});
    apiFetch<StrategyHealth>('/api/analytics/strategy-health').then(setStratHealth).catch(() => {});
    apiFetch<CheckpointData>('/api/analytics/checkpoint').then(setCheckpoint).catch(() => {});
    apiFetch<EdgeCalibration>('/api/analytics/edge-calibration').then(setEdgeCalib).catch(() => {});
  }, []);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-bold">Analytics</h1>
        <p className="text-sm text-gray-500">Performance metrics, equity curve, and edge decay analysis</p>
      </div>

      {metrics && (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <MetricCard label="Total Trades" value={String(metrics.total_trades)} />
          <MetricCard label="Win Rate" value={fmtPct(metrics.win_rate)} />
          <MetricCard label="Gross PnL" value={fmtUsd(metrics.gross_pnl)} />
          <MetricCard label="Net PnL" value={fmtUsd(metrics.net_pnl)} />
          <MetricCard label="Total Fees" value={fmtUsd(metrics.total_fees)} />
          <MetricCard label="Total Slippage" value={fmtUsd(metrics.total_slippage)} />
          <MetricCard label="Avg Win" value={fmtUsd(metrics.avg_win)} />
          <MetricCard label="Profit Factor" value={fmtNum(metrics.profit_factor)} />
        </div>
      )}

      {/* Expectancy panel — key research metric */}
      {metrics && (
        <div className="rounded-xl border border-indigo-500/20 bg-surface-raised p-5">
          <div className="mb-3 flex items-center gap-2">
            <h2 className="text-sm font-semibold text-gray-300">Edge Quality</h2>
            <span className="rounded bg-indigo-500/20 px-1.5 py-0.5 text-[10px] text-indigo-400">PER-TRADE</span>
          </div>
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <div>
              <p className="text-xs text-gray-500">Expectancy</p>
              <p className={`mt-1 text-xl font-bold tabular-nums ${metrics.expectancy > 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                {metrics.expectancy > 0 ? '+' : ''}{fmtUsd(metrics.expectancy)}
              </p>
              <p className="text-[10px] text-gray-600">per trade</p>
            </div>
            <div>
              <p className="text-xs text-gray-500">Expectancy %</p>
              <p className={`mt-1 text-xl font-bold tabular-nums ${(metrics.expectancy_pct ?? 0) > 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                {(metrics.expectancy_pct ?? 0) > 0 ? '+' : ''}{(metrics.expectancy_pct ?? 0).toFixed(3)}%
              </p>
              <p className="text-[10px] text-gray-600">of $900 bankroll</p>
            </div>
            <div>
              <p className="text-xs text-gray-500">Profit Factor</p>
              <p className={`mt-1 text-xl font-bold tabular-nums ${(metrics.profit_factor ?? 0) >= 1.3 ? 'text-emerald-400' : (metrics.profit_factor ?? 0) >= 1 ? 'text-yellow-400' : 'text-red-400'}`}>
                {metrics.profit_factor === Infinity ? 'INF' : (metrics.profit_factor ?? 0).toFixed(3)}
              </p>
              <p className="text-[10px] text-gray-600">
                {metrics.profit_factor >= 1.6 ? 'strong edge' : metrics.profit_factor >= 1.3 ? 'real edge' : metrics.profit_factor >= 1 ? 'weak edge' : 'no edge'}
              </p>
            </div>
            <div>
              <p className="text-xs text-gray-500">Avg Trade PnL</p>
              <p className={`mt-1 text-xl font-bold tabular-nums ${metrics.avg_trade_pnl > 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                {metrics.avg_trade_pnl > 0 ? '+' : ''}{fmtUsd(metrics.avg_trade_pnl)}
              </p>
              <p className="text-[10px] text-gray-600">gross per position</p>
            </div>
          </div>
          {/* Edge quality interpretation */}
          <div className="mt-3 rounded-lg bg-white/5 px-3 py-2 text-xs text-gray-400">
            {metrics.profit_factor >= 1.6
              ? 'Strong edge detected. System consistently outperforming costs.'
              : metrics.profit_factor >= 1.3
              ? 'Real but small edge. Keep accumulating sample size (target 200+ trades).'
              : metrics.profit_factor >= 1.0
              ? 'Marginal edge. Signal cost ratio needs improvement — check spread vs raw edge.'
              : 'No edge. Review filter thresholds in Experiment Lab.'}
            {' '}Spread-to-edge ratio: costs {fmtUsd(metrics.total_fees + metrics.total_slippage)} vs gross {fmtUsd(metrics.gross_pnl)}.
          </div>
        </div>
      )}

      {/* Equity Curve */}
      <div className="rounded-xl border border-white/5 bg-surface-raised p-5">
        <h2 className="mb-3 text-sm font-semibold text-gray-300">Equity Curve</h2>
        {equity.length > 0 ? (
          <div className="flex h-48 items-end gap-0.5">
            {equity.map((point, i) => {
              const min = Math.min(...equity.map((e) => e.equity));
              const max = Math.max(...equity.map((e) => e.equity));
              const range = max - min || 1;
              const height = ((point.equity - min) / range) * 100;
              return (
                <div
                  key={i}
                  className="flex-1 rounded-t bg-accent-blue/60 transition-colors hover:bg-accent-blue"
                  style={{ height: `${Math.max(2, height)}%` }}
                  title={`${fmtUsd(point.equity)} @ ${point.time}`}
                />
              );
            })}
          </div>
        ) : (
          <p className="text-sm text-gray-500">No equity data yet. Run the simulation to generate data.</p>
        )}
      </div>

      {/* Latency Decay Analysis  --  THE KEY RESEARCH INSIGHT */}
      <div className="rounded-xl border border-yellow-500/20 bg-surface-raised p-5">
        <div className="mb-4 flex items-start justify-between">
          <div>
            <h2 className="text-sm font-semibold text-gray-300">
              Latency Decay Analysis
              <span className="ml-2 rounded bg-yellow-500/20 px-1.5 py-0.5 text-[10px] text-yellow-400">RESEARCH INSIGHT</span>
            </h2>
            <p className="mt-1 text-xs text-gray-500">
              How much edge remains as copy latency increases. Green = still profitable. Red = edge dead.
            </p>
          </div>
          <a href="/wallets" className="text-xs text-accent-blue hover:underline">
            View per-wallet ->
          </a>
        </div>
        <LatencyDecayChart sweep={decaySweep} />
      </div>

      {/* Exit Reason Breakdown */}
      <div className="rounded-xl border border-white/5 bg-surface-raised p-5">
        <div className="mb-4 flex items-start justify-between">
          <div>
            <h2 className="text-sm font-semibold text-gray-300">
              Exit Reason Breakdown
              <span className="ml-2 rounded bg-purple-500/20 px-1.5 py-0.5 text-[10px] text-purple-400">HOW MONEY IS MADE</span>
            </h2>
            <p className="mt-1 text-xs text-gray-500">
              Why positions closed — reveals whether edge comes from timing, stops, or market resolution.
            </p>
          </div>
        </div>
        {exitBreakdown ? (
          <ExitBreakdownPanel data={exitBreakdown} />
        ) : (
          <p className="text-sm text-gray-500">Loading exit data…</p>
        )}
      </div>

      {/* Actionability Precision Trend */}
      {precision && (
        <div className="rounded-xl border border-cyan-500/20 bg-surface-raised p-5">
          <div className="mb-4 flex items-start justify-between">
            <div>
              <h2 className="text-sm font-semibold text-gray-300">
                Actionability Precision
                <span className="ml-2 rounded bg-cyan-500/20 px-1.5 py-0.5 text-[10px] text-cyan-400">IQ TEST</span>
              </h2>
              <p className="mt-1 text-xs text-gray-500">
                {precision.interpretation}
              </p>
            </div>
            <a href="/markets" className="text-xs text-accent-blue hover:underline">
              Markets ->
            </a>
          </div>

          {/* Grade + window comparison */}
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-4">
            {/* Overall grade */}
            <div className="flex flex-col items-center justify-center rounded-xl border border-white/5 p-4">
              <p className="text-xs text-gray-500">Overall Grade</p>
              <p className={`mt-1 text-4xl font-black ${
                precision.grade === 'A' ? 'text-emerald-400' :
                precision.grade === 'B' ? 'text-yellow-400' :
                precision.grade === 'C' ? 'text-orange-400' :
                precision.grade === 'D' ? 'text-red-400' : 'text-gray-500'
              }`}>
                {precision.grade}
              </p>
              <p className="mt-1 text-[10px] text-gray-600">all-time</p>
            </div>

            {/* 3 time windows */}
            {(['24h', '7d', 'all_time'] as const).map((key) => {
              const w = precision.windows[key as keyof typeof precision.windows];
              const pct = w.precision !== null ? w.precision * 100 : null;
              return (
                <div key={key} className="rounded-xl border border-white/5 p-4">
                  <p className="text-xs text-gray-500">
                    {key === '24h' ? 'Last 24h' : key === '7d' ? 'Last 7 days' : 'All time'}
                  </p>
                  <div className="mt-2 flex items-end gap-2">
                    <span className={`text-2xl font-bold ${
                      pct === null ? 'text-gray-500' :
                      pct >= 60 ? 'text-emerald-400' : pct >= 40 ? 'text-yellow-400' : 'text-red-400'
                    }`}>
                      {pct !== null ? `${pct.toFixed(0)}%` : 'N/A'}
                    </span>
                    <span className="mb-0.5 text-xs text-gray-500">
                      {w.profitable}/{w.total_closed}
                    </span>
                  </div>
                  {w.avg_pnl !== null && (
                    <p className={`mt-1 text-xs ${w.avg_pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                      avg {w.avg_pnl >= 0 ? '+' : ''}{w.avg_pnl.toFixed(2)} USD/trade
                    </p>
                  )}
                  {/* Mini bar */}
                  {pct !== null && (
                    <div className="mt-2 h-1.5 w-full rounded-full bg-white/5">
                      <div
                        className={`h-full rounded-full ${pct >= 60 ? 'bg-emerald-500' : pct >= 40 ? 'bg-yellow-500' : 'bg-red-500'}`}
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          {/* Diagnosis strip */}
          <div className="mt-4 rounded-lg bg-white/5 p-3 text-xs text-gray-400">
            <strong className="text-gray-300">How to improve grade: </strong>
            Increase <code className="text-cyan-400">min_z</code> threshold, raise <code className="text-cyan-400">min_depth</code>,
            or tighten <code className="text-cyan-400">max_spread_pct</code>.
            Use the <a href="/research" className="text-accent-blue hover:underline">Research Lab / Experiment Lab</a> to
            simulate parameter combinations before applying them.
          </div>
        </div>
      )}

      {/* Stabilization Checkpoint ─────────────────────────────────────────── */}
      {checkpoint && (
        <div className="rounded-xl border border-violet-500/20 bg-surface-raised p-5">
          <div className="mb-4 flex items-start justify-between gap-4">
            <div>
              <h2 className="text-sm font-semibold text-gray-300">Stabilization Checkpoint</h2>
              <p className="mt-0.5 text-xs text-gray-500">
                Real trading exits only — excludes stale_data / cleanup infrastructure exits
              </p>
            </div>
            {/* Progress to next milestone */}
            <div className="text-right text-xs">
              {checkpoint.summary.next_milestone ? (
                <>
                  <p className="text-gray-400">
                    Next milestone: <span className="font-semibold text-violet-400">{checkpoint.summary.next_milestone}</span>
                  </p>
                  <p className="text-gray-600">{checkpoint.summary.trades_until_next} more trades needed</p>
                </>
              ) : (
                <span className="rounded bg-emerald-500/15 px-2 py-0.5 text-emerald-400">All milestones reached</span>
              )}
            </div>
          </div>

          {/* Summary row */}
          <div className="mb-4 grid grid-cols-3 gap-3 sm:grid-cols-5">
            {[
              { label: 'Real Exits', value: String(checkpoint.summary.total_real_trading_exits), color: 'text-white' },
              { label: 'Infra Exits', value: String(checkpoint.summary.total_infra_exits), color: 'text-gray-500' },
              { label: 'Open Now', value: String(checkpoint.summary.open_positions), color: 'text-blue-400' },
              { label: 'Current Milestone', value: checkpoint.summary.current_milestone ? `${checkpoint.summary.current_milestone}` : '—', color: 'text-violet-400' },
              { label: 'Next Goal', value: checkpoint.summary.next_milestone ? `${checkpoint.summary.next_milestone}` : 'Done', color: 'text-gray-400' },
            ].map(({ label, value, color }) => (
              <div key={label} className="rounded-lg bg-white/3 p-3 text-center">
                <p className="text-[10px] text-gray-500">{label}</p>
                <p className={`mt-1 text-lg font-bold tabular-nums ${color}`}>{value}</p>
              </div>
            ))}
          </div>

          {/* Milestone cards */}
          {checkpoint.checkpoints.length === 0 ? (
            <p className="text-sm text-gray-500">
              No milestone reached yet. Need {checkpoint.summary.next_milestone} real trading exits (currently {checkpoint.summary.total_real_trading_exits}).
            </p>
          ) : (
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {[...(checkpoint.all_time ? [{ ...checkpoint.all_time, label: 'all_time' }] : []), ...checkpoint.checkpoints].map((cp) => {
                const verdictColor =
                  cp.verdict === 'strong_edge'    ? 'border-emerald-500/40 bg-emerald-500/5' :
                  cp.verdict === 'positive_edge'  ? 'border-green-500/30 bg-green-500/5' :
                  cp.verdict === 'marginal'        ? 'border-yellow-500/30 bg-yellow-500/5' :
                  cp.verdict === 'weak'            ? 'border-orange-500/30 bg-orange-500/5' :
                  cp.verdict === 'losing'          ? 'border-red-500/30 bg-red-500/5' :
                                                    'border-white/10 bg-white/3';
                const pfColor =
                  (cp.profit_factor ?? 0) >= 1.5  ? 'text-emerald-400' :
                  (cp.profit_factor ?? 0) >= 1.1  ? 'text-green-400' :
                  (cp.profit_factor ?? 0) >= 0.9  ? 'text-yellow-400' :
                                                    'text-red-400';
                const costColor =
                  (cp.cost_gross_ratio ?? 99) <= 0.5 ? 'text-emerald-400' :
                  (cp.cost_gross_ratio ?? 99) <= 1.0 ? 'text-yellow-400' :
                                                       'text-red-400';

                return (
                  <div key={cp.label} className={`rounded-xl border p-4 ${verdictColor}`}>
                    {/* Header */}
                    <div className="mb-3 flex items-center justify-between">
                      <span className="text-xs font-semibold uppercase tracking-wider text-gray-400">
                        {cp.label === 'all_time' ? `All Time (${cp.trade_count})` : `Last ${cp.window_n} trades`}
                      </span>
                      <span className={`rounded px-2 py-0.5 text-[10px] font-semibold uppercase ${
                        cp.verdict === 'strong_edge'   ? 'bg-emerald-500/20 text-emerald-400' :
                        cp.verdict === 'positive_edge' ? 'bg-green-500/20 text-green-400' :
                        cp.verdict === 'marginal'      ? 'bg-yellow-500/20 text-yellow-400' :
                        cp.verdict === 'weak'          ? 'bg-orange-500/20 text-orange-400' :
                        cp.verdict === 'losing'        ? 'bg-red-500/20 text-red-400' :
                                                        'bg-white/5 text-gray-400'
                      }`}>{cp.verdict.replace('_', ' ')}</span>
                    </div>

                    {/* Core metrics */}
                    <div className="mb-3 grid grid-cols-2 gap-x-4 gap-y-2 text-xs">
                      <div>
                        <p className="text-gray-500">Profit Factor</p>
                        <p className={`text-lg font-bold tabular-nums ${pfColor}`}>
                          {cp.profit_factor === null ? '—' : cp.profit_factor === Infinity ? '∞' : cp.profit_factor.toFixed(3)}
                        </p>
                      </div>
                      <div>
                        <p className="text-gray-500">Expectancy</p>
                        <p className={`text-lg font-bold tabular-nums ${cp.expectancy >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                          {cp.expectancy >= 0 ? '+' : ''}{cp.expectancy.toFixed(4)}
                        </p>
                      </div>
                      <div>
                        <p className="text-gray-500">Cost/Gross</p>
                        <p className={`text-base font-semibold tabular-nums ${costColor}`}>
                          {cp.cost_gross_ratio === null ? '—' : `${cp.cost_gross_ratio.toFixed(2)}x`}
                        </p>
                      </div>
                      <div>
                        <p className="text-gray-500">Win Rate</p>
                        <p className={`text-base font-semibold tabular-nums ${cp.win_rate >= 0.5 ? 'text-green-400' : 'text-orange-400'}`}>
                          {(cp.win_rate * 100).toFixed(0)}%
                        </p>
                      </div>
                    </div>

                    {/* BUY vs SELL */}
                    <div className="mb-3 flex gap-3 text-[11px]">
                      <span className="rounded bg-blue-500/10 px-2 py-0.5 text-blue-300">
                        BUY {cp.buy_count} · {cp.buy_win_rate != null ? `${(cp.buy_win_rate * 100).toFixed(0)}% win` : '—'}
                      </span>
                      <span className="rounded bg-orange-500/10 px-2 py-0.5 text-orange-300">
                        SELL {cp.sell_count} · {cp.sell_win_rate != null ? `${(cp.sell_win_rate * 100).toFixed(0)}% win` : '—'}
                      </span>
                    </div>

                    {/* Avg size + hold */}
                    <div className="mb-3 flex gap-4 text-[11px] text-gray-500">
                      <span>Avg size <span className="text-gray-300">${cp.avg_position_size_usd.toFixed(2)}</span></span>
                      {cp.avg_hold_minutes != null && (
                        <span>Avg hold <span className="text-gray-300">{cp.avg_hold_minutes.toFixed(0)}m</span></span>
                      )}
                    </div>

                    {/* Exit breakdown mini */}
                    <div className="space-y-1">
                      <p className="text-[10px] text-gray-600 uppercase tracking-wider">Exit reasons</p>
                      {Object.entries(cp.exit_breakdown).slice(0, 4).map(([reason, count]) => {
                        const meta = EXIT_REASON_META[reason] ?? EXIT_REASON_META.unknown;
                        const pct = Math.round((count / cp.trade_count) * 100);
                        return (
                          <div key={reason} className="flex items-center gap-2 text-[11px]">
                            <span className={`w-14 shrink-0 ${meta.color}`}>{meta.icon} {reason.replace('_', ' ')}</span>
                            <div className="flex-1 overflow-hidden rounded bg-white/5">
                              <div className={`h-1.5 rounded ${meta.color.replace('text-', 'bg-')}/50`} style={{ width: `${pct}%` }} />
                            </div>
                            <span className="w-8 text-right text-gray-500">{count}</span>
                          </div>
                        );
                      })}
                    </div>

                    {/* Strategy PnL */}
                    {Object.keys(cp.strategy_pnl).length > 0 && (
                      <div className="mt-3 space-y-0.5">
                        <p className="text-[10px] text-gray-600 uppercase tracking-wider">Strategy PnL</p>
                        {Object.entries(cp.strategy_pnl).map(([strat, pnl]) => (
                          <div key={strat} className="flex justify-between text-[11px]">
                            <span className="text-gray-400">{strat}</span>
                            <span className={pnl >= 0 ? 'text-green-400' : 'text-red-400'}>
                              {pnl >= 0 ? '+' : ''}{pnl.toFixed(4)}
                            </span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* Strategy Health — kill switch + rolling performance + capital allocation */}
      {stratHealth && (
        <div className="rounded-xl border border-amber-500/20 bg-surface-raised p-5">
          <div className="mb-4 flex items-start justify-between">
            <div>
              <h2 className="text-sm font-semibold text-gray-300">
                Strategy Health
                <span className="ml-2 rounded bg-amber-500/20 px-1.5 py-0.5 text-[10px] text-amber-400">LIVE ALLOCATION</span>
              </h2>
              <p className="mt-1 text-xs text-gray-500">
                Rolling {stratHealth.rolling_window}-trade performance &bull; Capital weights &bull; Auto kill-switch status
              </p>
            </div>
            <div className="flex gap-2 text-[10px]">
              <span className="rounded bg-emerald-500/10 px-2 py-1 text-emerald-400">{stratHealth.summary.active_count} active</span>
              <span className="rounded bg-gray-500/10 px-2 py-1 text-gray-400">{stratHealth.summary.shadow_count} shadow</span>
              {stratHealth.summary.kill_active_count > 0 && (
                <span className="rounded bg-red-500/10 px-2 py-1 text-red-400">{stratHealth.summary.kill_active_count} killed</span>
              )}
            </div>
          </div>

          <div className="space-y-3">
            {stratHealth.strategies.filter(s => s.strategy !== 'shadow').map((s) => {
              const modeColor = s.mode === 'active' ? 'bg-emerald-500' : s.mode === 'paused' ? 'bg-red-500' : 'bg-gray-500';
              const verdictBg =
                s.verdict === 'strong' ? 'text-emerald-400 bg-emerald-500/10' :
                s.verdict === 'marginal' ? 'text-yellow-400 bg-yellow-500/10' :
                s.verdict === 'kill_switch' ? 'text-red-400 bg-red-500/10' :
                s.verdict === 'shadow_monitoring' ? 'text-gray-400 bg-gray-500/10' :
                'text-red-400 bg-red-500/10';
              const trendIcon = s.trend === 'improving' ? '↑' : s.trend === 'deteriorating' ? '↓' : '→';
              const trendColor = s.trend === 'improving' ? 'text-emerald-400' : s.trend === 'deteriorating' ? 'text-red-400' : 'text-gray-500';
              const pf = s.rolling.profit_factor;
              const pfColor = pf == null ? 'text-gray-500' : pf >= 1.5 ? 'text-emerald-400' : pf >= 1.0 ? 'text-yellow-400' : 'text-red-400';

              return (
                <div key={s.strategy} className="rounded-lg border border-white/5 p-3">
                  {/* Header row */}
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span className={`h-2 w-2 rounded-full ${modeColor}`} />
                      <span className="text-sm font-semibold text-white">{s.strategy}</span>
                      <span className={`rounded px-1.5 py-0.5 text-[9px] font-bold uppercase ${verdictBg}`}>
                        {s.verdict.replace('_', ' ')}
                      </span>
                      {s.kill_active && (
                        <span className="rounded bg-red-900/40 px-1.5 py-0.5 text-[9px] font-bold text-red-400">KILL ACTIVE</span>
                      )}
                    </div>
                    <div className="flex items-center gap-3 text-xs">
                      <span className={trendColor}>{trendIcon} {s.trend}</span>
                      <span className="text-gray-400">
                        Capital: <span className={s.mode === 'active' ? 'font-bold text-white' : 'text-gray-500'}>
                          {(s.capital_share * 100).toFixed(0)}%
                        </span>
                      </span>
                    </div>
                  </div>

                  {/* Metrics row */}
                  <div className="mt-2 grid grid-cols-4 gap-2 text-[10px] sm:grid-cols-6">
                    <div>
                      <p className="text-gray-500">Trades</p>
                      <p className="text-gray-300">{s.rolling.trade_count}</p>
                    </div>
                    <div>
                      <p className="text-gray-500">Win Rate</p>
                      <p className="text-gray-300">{fmtPct(s.rolling.win_rate)}</p>
                    </div>
                    <div>
                      <p className="text-gray-500">Rolling PF</p>
                      <p className={pfColor}>{pf != null ? pf.toFixed(2) : 'N/A'}</p>
                    </div>
                    <div>
                      <p className="text-gray-500">Expectancy</p>
                      <p className={s.rolling.expectancy >= 0 ? 'text-emerald-400' : 'text-red-400'}>
                        {s.rolling.expectancy >= 0 ? '+' : ''}{s.rolling.expectancy.toFixed(3)}
                      </p>
                    </div>
                    <div>
                      <p className="text-gray-500">7d PF</p>
                      <p className={
                        s.last_7d.profit_factor == null ? 'text-gray-500' :
                        s.last_7d.profit_factor >= 1.3 ? 'text-emerald-400' : 'text-yellow-400'
                      }>
                        {s.last_7d.profit_factor != null ? s.last_7d.profit_factor.toFixed(2) : '—'}
                      </p>
                    </div>
                    <div>
                      <p className="text-gray-500">Mode</p>
                      <p className={s.mode === 'active' ? 'text-emerald-400' : 'text-gray-500'}>{s.mode}</p>
                    </div>
                  </div>

                  {/* Capital share bar */}
                  {s.mode === 'active' && s.capital_share > 0 && (
                    <div className="mt-2">
                      <div className="h-1 w-full rounded-full bg-white/5">
                        <div
                          className="h-full rounded-full bg-emerald-500/60 transition-all"
                          style={{ width: `${Math.min(100, s.capital_share * 100)}%` }}
                        />
                      </div>
                    </div>
                  )}

                  {/* Kill reason */}
                  {s.kill_reason && (
                    <p className="mt-2 text-[10px] text-red-400">{s.kill_reason}</p>
                  )}
                </div>
              );
            })}
          </div>

          {/* Capital allocation note */}
          <div className="mt-3 rounded-lg bg-white/5 px-3 py-2 text-[11px] text-gray-500">
            Total allocated: <span className="text-white">{(stratHealth.summary.total_capital_allocated * 100).toFixed(0)}%</span>
            {stratHealth.summary.total_capital_allocated > 1.0 && (
              <span className="ml-1 text-yellow-400">(shares are per-strategy caps; total bankroll is shared)</span>
            )}
            {' — '}Dislocation is in <span className="text-gray-400">shadow mode</span> until rolling PF &ge; 1.2.
            Kill-switch fires when rolling PF &lt; 0.8 over last {stratHealth.rolling_window} trades.
          </div>
        </div>
      )}

      {/* Per-Strategy + Per-Wallet */}
      <div className="grid gap-4 md:grid-cols-2">        <div className="rounded-xl border border-white/5 bg-surface-raised p-5">
          <h2 className="mb-3 text-sm font-semibold text-gray-300">Per-Strategy Performance</h2>
          {perStrategy.length > 0 ? (
            <div className="space-y-3">
              {perStrategy.map((s) => {
                const verdictColor = s.verdict === 'strong' ? 'text-emerald-400 bg-emerald-500/10' :
                  s.verdict === 'marginal' ? 'text-yellow-400 bg-yellow-500/10' : 'text-red-400 bg-red-500/10';
                return (
                  <div key={s.strategy} className="rounded-lg border border-white/5 p-3 space-y-2">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-medium text-white">{s.strategy}</span>
                        {s.verdict && (
                          <span className={`rounded px-1.5 py-0.5 text-[9px] font-bold uppercase ${verdictColor}`}>
                            {s.verdict}
                          </span>
                        )}
                      </div>
                      <span className={`text-sm font-bold ${s.total_pnl >= 0 ? 'text-accent-green' : 'text-accent-red'}`}>
                        {fmtUsd(s.total_pnl)}
                        {s.pnl_share_pct !== undefined && (
                          <span className="ml-1 text-[10px] text-gray-500">({s.pnl_share_pct > 0 ? '+' : ''}{s.pnl_share_pct.toFixed(0)}%)</span>
                        )}
                      </span>
                    </div>
                    <div className="grid grid-cols-3 gap-2 text-[10px]">
                      <div>
                        <p className="text-gray-500">Trades / WR</p>
                        <p className="text-gray-300">{s.trade_count} / {fmtPct(s.win_rate)}</p>
                      </div>
                      <div>
                        <p className="text-gray-500">Profit Factor</p>
                        <p className={s.profit_factor != null && s.profit_factor >= 1.3 ? 'text-emerald-400' : 'text-gray-300'}>
                          {s.profit_factor != null ? s.profit_factor.toFixed(2) : 'inf'}
                        </p>
                      </div>
                      <div>
                        <p className="text-gray-500">Expectancy</p>
                        <p className={s.expectancy != null && s.expectancy >= 0 ? 'text-emerald-400' : 'text-red-400'}>
                          {s.expectancy != null ? `${s.expectancy >= 0 ? '+' : ''}${s.expectancy.toFixed(3)}` : 'N/A'}
                        </p>
                      </div>
                    </div>
                    {s.max_drawdown != null && s.max_drawdown > 0.05 && (
                      <p className="text-[10px] text-orange-400">DD: {fmtPct(s.max_drawdown)}</p>
                    )}
                  </div>
                );
              })}
            </div>
          ) : (
            <p className="text-sm text-gray-500">No strategy data</p>
          )}
        </div>

        <div className="rounded-xl border border-white/5 bg-surface-raised p-5">
          <h2 className="mb-3 text-sm font-semibold text-gray-300">Per-Wallet Performance</h2>
          {perWallet.length > 0 ? (
            <div className="space-y-2">
              {perWallet.map((w) => (
                <div key={w.wallet_id} className="flex items-center justify-between border-b border-white/5 py-1.5">
                  <span className="font-mono text-xs">{w.wallet_id.slice(0, 12)}...</span>
                  <div className="flex gap-4 text-xs">
                    <span className="text-gray-400">{w.trade_count} trades</span>
                    <span className="text-gray-400">{fmtPct(w.win_rate)} WR</span>
                    <span className={w.total_pnl >= 0 ? 'text-accent-green' : 'text-accent-red'}>{fmtUsd(w.total_pnl)}</span>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm text-gray-500">No wallet data</p>
          )}
        </div>
      </div>

      {/* ── Edge Calibration ─────────────────────────────────────────────── */}
      {edgeCalib && !('error' in edgeCalib) && (
        <div className="rounded-xl border border-orange-500/20 bg-surface-raised p-5">
          <div className="mb-4 flex items-start justify-between">
            <div>
              <h2 className="text-sm font-semibold text-gray-300">
                Edge Calibration
                <span className="ml-2 rounded bg-orange-500/20 px-1.5 py-0.5 text-[10px] text-orange-400">MODEL IQ</span>
              </h2>
              <p className="mt-1 text-xs text-gray-500">
                Does predicted edge_at_entry actually correlate with realized PnL?
              </p>
            </div>
            <span className="text-xs text-gray-600">{edgeCalib.sample_size} samples</span>
          </div>

          {/* Overall verdict */}
          <div className={`mb-4 rounded-lg border px-4 py-3 ${
            edgeCalib.overall.verdict === 'well_calibrated'    ? 'border-emerald-500/30 bg-emerald-500/10' :
            edgeCalib.overall.verdict === 'weakly_calibrated'  ? 'border-yellow-500/30 bg-yellow-500/10' :
            edgeCalib.overall.verdict === 'inverted'           ? 'border-red-500/30 bg-red-500/10' :
                                                                  'border-gray-500/30 bg-gray-500/10'
          }`}>
            <div className="flex items-center gap-3">
              <span className={`text-2xl font-black ${
                edgeCalib.overall.verdict === 'well_calibrated'   ? 'text-emerald-400' :
                edgeCalib.overall.verdict === 'weakly_calibrated' ? 'text-yellow-400' :
                edgeCalib.overall.verdict === 'inverted'          ? 'text-red-400' : 'text-gray-400'
              }`}>
                r = {edgeCalib.overall.pearson_r !== null ? edgeCalib.overall.pearson_r.toFixed(3) : '—'}
              </span>
              <div>
                <p className="text-xs font-semibold text-white capitalize">{edgeCalib.overall.verdict.replace(/_/g, ' ')}</p>
                <p className="text-[11px] text-gray-400">{edgeCalib.overall.interpretation}</p>
              </div>
            </div>
          </div>

          {/* BUY / SELL / Strategy split */}
          <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
            {[
              { label: 'BUY r', value: edgeCalib.by_side.buy.pearson_r,  verdict: edgeCalib.by_side.buy.verdict },
              { label: 'SELL r', value: edgeCalib.by_side.sell.pearson_r, verdict: edgeCalib.by_side.sell.verdict },
              { label: 'direct_copy r', value: edgeCalib.by_strategy.direct_copy.pearson_r, verdict: null },
              { label: 'high_conv r',   value: edgeCalib.by_strategy.high_conviction.pearson_r, verdict: null },
            ].map((item) => (
              <div key={item.label} className="rounded-lg border border-white/5 p-3">
                <p className="text-[10px] text-gray-500">{item.label}</p>
                <p className={`mt-1 text-lg font-bold tabular-nums ${
                  item.value === null ? 'text-gray-600' :
                  item.value >= 0.3   ? 'text-emerald-400' :
                  item.value >= 0     ? 'text-yellow-400' :
                                        'text-red-400'
                }`}>
                  {item.value !== null ? item.value.toFixed(3) : '—'}
                </p>
                {item.verdict && (
                  <p className="text-[9px] text-gray-600 capitalize">{item.verdict.replace(/_/g, ' ')}</p>
                )}
              </div>
            ))}
          </div>

          {/* Edge bucket breakdown */}
          <div className="mb-4">
            <p className="mb-2 text-xs text-gray-500">Average realized PnL by entry edge bucket</p>
            <div className="flex gap-3">
              {edgeCalib.edge_buckets.map((b) => {
                const pnl = b.avg_pnl;
                const color = pnl === null ? 'text-gray-600' : pnl > 0 ? 'text-emerald-400' : 'text-red-400';
                return (
                  <div key={b.bucket} className="flex-1 rounded-lg border border-white/5 p-3 text-center">
                    <p className="text-[10px] text-gray-500">{b.threshold}</p>
                    <p className={`mt-1 text-base font-bold ${color}`}>
                      {pnl !== null ? `${pnl >= 0 ? '+' : ''}$${pnl.toFixed(4)}` : '—'}
                    </p>
                    <p className="text-[9px] text-gray-600">n={b.n}</p>
                  </div>
                );
              })}
            </div>
          </div>

          {/* High-edge miss analysis */}
          {edgeCalib.high_edge_miss_analysis.high_edge_total > 0 && (
            <div className="rounded-lg border border-red-500/20 bg-red-500/5 p-3">
              <p className="mb-2 text-xs font-semibold text-red-400">
                High-Edge Miss Analysis (edge &gt; 0.3)
                {' '}&mdash; {edgeCalib.high_edge_miss_analysis.high_edge_losses}/{edgeCalib.high_edge_miss_analysis.high_edge_total} losses
                {edgeCalib.high_edge_miss_analysis.miss_rate !== null && (
                  <span className="ml-1">({(edgeCalib.high_edge_miss_analysis.miss_rate * 100).toFixed(0)}% miss rate)</span>
                )}
              </p>
              <div className="flex gap-4 text-[11px] text-gray-400">
                <span>wallet_reversal: <span className="text-white">{edgeCalib.high_edge_miss_analysis.by_exit_reason.wallet_reversal}</span></span>
                <span>stop_loss: <span className="text-white">{edgeCalib.high_edge_miss_analysis.by_exit_reason.stop_loss}</span></span>
                <span>max_hold: <span className="text-white">{edgeCalib.high_edge_miss_analysis.by_exit_reason.max_hold_time}</span></span>
              </div>
              <p className="mt-2 text-[11px] text-orange-400">{edgeCalib.high_edge_miss_analysis.diagnosis}</p>
            </div>
          )}

          {/* Interpretation note */}
          <p className="mt-3 text-[11px] text-gray-600">{edgeCalib.note}</p>
        </div>
      )}

    </div>
  );
}
