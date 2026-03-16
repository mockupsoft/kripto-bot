'use client';

import { useEffect, useState } from 'react';
import { apiFetch } from '@/lib/api';
import { fmtUsd, fmtPct } from '@/lib/format';
import type { OverviewData } from '@/lib/types';

interface ActionSummary { tradeable: number; marginal: number; total: number }
interface PrecisionSummary { grade: string; interpretation: string; all_time_precision: number | null; all_time_total: number }
interface DigestSummary {
  edge_verdict: string;
  recommendation: string;
  sample_size: number;
  sample_progress_pct: number;
  sample_status: string;
}
interface DigestWatchlist {
  profit_factor: number | null;
  expectancy_usd: number | null;
  win_rate: number | null;
  precision_24h: number | null;
}
interface CostEfficiency {
  cost_to_gross_ratio: number | null;
  interpretation: string;
}

function StatCard({
  label, value, sub, accent, href,
}: { label: string; value: string; sub?: string; accent?: 'green' | 'red' | 'amber'; href?: string }) {
  const accentColor = accent === 'green' ? 'text-emerald-400' : accent === 'red' ? 'text-red-400' : accent === 'amber' ? 'text-amber-400' : '';
  const inner = (
    <div className={`rounded-xl border p-5 transition-colors ${accent === 'green' ? 'border-emerald-500/30 bg-emerald-500/5' : accent === 'amber' ? 'border-amber-500/30 bg-amber-500/5' : 'border-white/5 bg-surface-raised'} ${href ? 'hover:border-white/10' : ''}`}>
      <p className="text-xs text-gray-500">{label}</p>
      <p className={`mt-1 text-2xl font-semibold tracking-tight ${accentColor}`}>{value}</p>
      {sub && <p className="mt-0.5 text-xs text-gray-500">{sub}</p>}
    </div>
  );
  if (href) return <a href={href}>{inner}</a>;
  return inner;
}

export default function OverviewPage() {
  const [data, setData] = useState<OverviewData | null>(null);
  const [action, setAction] = useState<ActionSummary | null>(null);
  const [prec, setPrec] = useState<PrecisionSummary | null>(null);
  const [digest, setDigest] = useState<{
    summary: DigestSummary;
    watchlist: DigestWatchlist;
    cost_efficiency: CostEfficiency;
  } | null>(null);

  useEffect(() => {
    const fetchAll = () => {
      apiFetch<OverviewData>('/api/overview').then(setData).catch(() => {});
      apiFetch<ActionSummary>('/api/markets/actionability')
        .then((d) => setAction({ tradeable: d.tradeable, marginal: d.marginal, total: d.total }))
        .catch(() => {});
      apiFetch<{ grade: string; interpretation: string; windows: { all_time: { precision: number | null; total_closed: number } } }>('/api/markets/actionability-precision')
        .then((d) => setPrec({
          grade: d.grade,
          interpretation: d.interpretation,
          all_time_precision: d.windows.all_time.precision,
          all_time_total: d.windows.all_time.total_closed,
        }))
        .catch(() => {});
      apiFetch<any>('/api/analytics/daily-digest')
        .then((d) => setDigest({ summary: d.summary, watchlist: d.watchlist, cost_efficiency: d.cost_efficiency }))
        .catch(() => {});
    };
    fetchAll();
    const id = setInterval(fetchAll, 8000);
    return () => clearInterval(id);
  }, []);

  if (!data) {
    return <div className="flex items-center justify-center h-full text-gray-500">Loading...</div>;
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-bold">Dashboard Overview</h1>
        <p className="text-sm text-gray-500">Paper trading simulation — starting balance $900</p>
      </div>

      {/* Portfolio row */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard label="Paper Balance" value={fmtUsd(data.paper_balance)} />
        <StatCard label="Total Equity" value={fmtUsd(data.total_equity)} />
        <StatCard
          label="Realized PnL"
          value={fmtUsd(data.realized_pnl)}
          sub={data.realized_pnl >= 0 ? 'Profit' : 'Loss'}
          accent={data.realized_pnl > 0 ? 'green' : data.realized_pnl < 0 ? 'red' : undefined}
        />
        <StatCard label="Unrealized PnL" value={fmtUsd(data.unrealized_pnl)}
          accent={data.unrealized_pnl > 0 ? 'green' : data.unrealized_pnl < 0 ? 'red' : undefined} />
      </div>

      {/* Activity row */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard label="Open Positions" value={String(data.open_positions)} sub="active paper trades" />
        <StatCard label="Total Signals" value={String(data.total_signals)} sub="all time" />
        <StatCard label="Max Drawdown" value={fmtPct(data.max_drawdown)}
          accent={data.max_drawdown > 0.15 ? 'red' : undefined} />
        <StatCard
          label="System Status"
          value={data.demo_mode ? 'DEMO' : '⚠ LIVE'}
          sub={data.demo_mode ? 'Paper trading only' : 'Real execution — danger!'}
          accent={data.demo_mode ? undefined : 'red'}
        />
      </div>

      {/* Daily Digest — 5 key metrics */}
      {digest && (
        <div className={`rounded-xl border p-5 ${
          digest.summary.edge_verdict === 'strong_edge' ? 'border-emerald-500/20 bg-emerald-500/5' :
          digest.summary.edge_verdict === 'real_edge' ? 'border-cyan-500/20 bg-cyan-500/5' :
          digest.summary.edge_verdict === 'marginal' ? 'border-yellow-500/20 bg-yellow-500/5' :
          'border-red-500/20 bg-red-500/5'
        }`}>
          <div className="mb-3 flex items-start justify-between">
            <div>
              <h2 className="text-sm font-semibold text-gray-300">
                Daily Digest
                <span className={`ml-2 rounded px-1.5 py-0.5 text-[10px] font-medium ${
                  digest.summary.edge_verdict === 'strong_edge' ? 'bg-emerald-500/20 text-emerald-400' :
                  digest.summary.edge_verdict === 'real_edge' ? 'bg-cyan-500/20 text-cyan-400' :
                  digest.summary.edge_verdict === 'marginal' ? 'bg-yellow-500/20 text-yellow-400' :
                  'bg-red-500/20 text-red-400'
                }`}>
                  {digest.summary.edge_verdict.toUpperCase().replace('_', ' ')}
                </span>
              </h2>
              <p className="mt-0.5 text-xs text-gray-500">{digest.summary.recommendation}</p>
            </div>
            <a href="/analytics" className="text-xs text-accent-blue hover:underline">Details</a>
          </div>

          <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
            {/* Profit Factor */}
            <div className="rounded-lg bg-white/5 p-3">
              <p className="text-[10px] text-gray-500">Profit Factor</p>
              <p className={`mt-1 text-lg font-bold tabular-nums ${
                (digest.watchlist.profit_factor ?? 0) >= 1.3 ? 'text-emerald-400' :
                (digest.watchlist.profit_factor ?? 0) >= 1 ? 'text-yellow-400' : 'text-red-400'
              }`}>
                {digest.watchlist.profit_factor?.toFixed(3) ?? 'N/A'}
              </p>
              <p className="text-[10px] text-gray-600">
                {(digest.watchlist.profit_factor ?? 0) >= 1.6 ? 'strong' :
                 (digest.watchlist.profit_factor ?? 0) >= 1.3 ? 'real edge' :
                 (digest.watchlist.profit_factor ?? 0) >= 1 ? 'weak' : 'no edge'}
              </p>
            </div>

            {/* Expectancy */}
            <div className="rounded-lg bg-white/5 p-3">
              <p className="text-[10px] text-gray-500">Expectancy</p>
              <p className={`mt-1 text-lg font-bold tabular-nums ${
                (digest.watchlist.expectancy_usd ?? 0) > 0 ? 'text-emerald-400' : 'text-red-400'
              }`}>
                {digest.watchlist.expectancy_usd != null
                  ? `${digest.watchlist.expectancy_usd > 0 ? '+' : ''}$${digest.watchlist.expectancy_usd.toFixed(3)}`
                  : 'N/A'}
              </p>
              <p className="text-[10px] text-gray-600">per trade</p>
            </div>

            {/* Win Rate */}
            <div className="rounded-lg bg-white/5 p-3">
              <p className="text-[10px] text-gray-500">Win Rate</p>
              <p className={`mt-1 text-lg font-bold tabular-nums ${
                (digest.watchlist.win_rate ?? 0) >= 0.6 ? 'text-emerald-400' : 'text-yellow-400'
              }`}>
                {digest.watchlist.win_rate != null ? `${(digest.watchlist.win_rate * 100).toFixed(1)}%` : 'N/A'}
              </p>
              <p className="text-[10px] text-gray-600">closed trades</p>
            </div>

            {/* Cost Ratio */}
            <div className="rounded-lg bg-white/5 p-3">
              <p className="text-[10px] text-gray-500">Cost / Gross</p>
              <p className={`mt-1 text-lg font-bold tabular-nums ${
                (digest.cost_efficiency.cost_to_gross_ratio ?? 99) < 0.5 ? 'text-emerald-400' :
                (digest.cost_efficiency.cost_to_gross_ratio ?? 99) < 1.0 ? 'text-yellow-400' : 'text-red-400'
              }`}>
                {digest.cost_efficiency.cost_to_gross_ratio != null
                  ? `${(digest.cost_efficiency.cost_to_gross_ratio * 100).toFixed(0)}%`
                  : 'N/A'}
              </p>
              <p className="text-[10px] text-gray-600">spread eating edge</p>
            </div>

            {/* Sample Progress */}
            <div className="rounded-lg bg-white/5 p-3">
              <p className="text-[10px] text-gray-500">Sample Size</p>
              <p className={`mt-1 text-lg font-bold tabular-nums ${
                digest.summary.sample_status === 'sufficient' ? 'text-emerald-400' :
                digest.summary.sample_status === 'building' ? 'text-cyan-400' :
                digest.summary.sample_status === 'low' ? 'text-yellow-400' : 'text-red-400'
              }`}>
                {digest.summary.sample_size}
              </p>
              <div className="mt-1 h-1 w-full rounded-full bg-white/10">
                <div
                  className="h-full rounded-full bg-cyan-500"
                  style={{ width: `${Math.min(100, digest.summary.sample_progress_pct)}%` }}
                />
              </div>
              <p className="mt-0.5 text-[10px] text-gray-600">{digest.summary.sample_progress_pct.toFixed(0)}% to 200</p>
            </div>
          </div>
        </div>
      )}

      {/* Dislocation / actionability row */}      <div>
        <h2 className="mb-3 text-sm font-semibold text-gray-400">
          Market Intelligence
          <span className="ml-2 text-[10px] font-normal text-gray-600">live dislocation radar</span>
        </h2>
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          <StatCard
            label="Tradeable Dislocations"
            value={action ? String(action.tradeable) : '…'}
            sub={action?.tradeable ? 'actionable right now' : 'no tradeable signal currently'}
            accent={action?.tradeable ? 'green' : undefined}
            href="/markets"
          />
          <StatCard
            label="Marginal Opportunities"
            value={action ? String(action.marginal) : '…'}
            sub="borderline edge — monitor closely"
            accent={action?.marginal ? 'amber' : undefined}
            href="/markets"
          />
          <StatCard
            label="Monitored Pairs"
            value={action ? String(action.total) : '…'}
            sub="active relationship pairs"
            href="/markets"
          />
          <StatCard
            label="Actionability Precision"
            value={prec
              ? (prec.all_time_precision != null
                  ? `${(prec.all_time_precision * 100).toFixed(0)}%`
                  : `Grade ${prec.grade}`)
              : '…'}
            sub={prec
              ? (prec.all_time_total > 0
                  ? `${prec.all_time_total} closed · Grade ${prec.grade}`
                  : 'No closed trades yet')
              : 'loading…'}
            accent={
              prec?.all_time_precision == null ? undefined
              : prec.all_time_precision >= 0.6 ? 'green'
              : prec.all_time_precision >= 0.4 ? 'amber'
              : 'red'
            }
            href="/markets"
          />
        </div>
      </div>
    </div>
  );
}

