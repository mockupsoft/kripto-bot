'use client';

import { useEffect, useState, useCallback, useRef } from 'react';
import { apiFetch } from '@/lib/api';
import { fmtPct } from '@/lib/format';
import type { WalletSummary } from '@/lib/types';

const POLL_MS = 15_000;

/* ─── Types ─────────────────────────────────────────────── */
interface AlphaEntry {
  rank: number;
  wallet_id: string;
  address: string;
  label: string;
  is_real: boolean;
  roi: number;
  hit_rate: number;
  copyability_score: number;
  composite_score: number;
  copyable_alpha: number;
  classification: string;
  max_drawdown: number;
  consistency: number;
  suspiciousness: number;
  latency_decay: { '200ms': number; '500ms': number; '1000ms': number; '1500ms': number };
  verdict: string;
}

interface WalletDetail {
  id: string;
  address: string;
  label: string;
  score: {
    composite: number | null;
    copyability: number | null;
    total_roi: number | null;
    hit_rate: number | null;
    max_drawdown: number | null;
    classification: string | null;
    consistency: number | null;
    suspiciousness: number | null;
    copy_decay_curve: Record<string, number>;
    explanation: Record<string, unknown>;
  } | null;
}

interface WalletTrade {
  id: string;
  market_id: string;
  side: string;
  outcome: string;
  price: number | null;
  size: number | null;
  notional: number | null;
  occurred_at: string | null;
  detected_at: string | null;
  detection_lag_ms: number | null;
}

/* ─── Helpers ────────────────────────────────────────────── */
const CLASSIFICATION_COLORS: Record<string, string> = {
  arbitrageur: 'bg-cyan-500/15 text-cyan-400',
  whale: 'bg-purple-500/15 text-purple-400',
  insider: 'bg-red-500/15 text-red-400',
  informed: 'bg-amber-500/15 text-amber-400',
  gambler: 'bg-orange-500/15 text-orange-400',
  unknown: 'bg-white/5 text-gray-400',
};

const VERDICT_REASONS: Record<string, string> = {
  STRONG_CANDIDATE: 'High copyability + positive ROI — actively monitor and copy',
  MODERATE: 'Borderline copyable — worth watching, needs more data',
  TOO_FAST: 'Edge dies at 500ms — requires sub-200ms execution infrastructure',
  NOT_COPYABLE: 'Edge evaporates before you can act — skip',
  AVOID: 'Copyable mechanics but consistently unprofitable — avoid',
  WEAK: 'Low confidence across all metrics — insufficient signal',
};

function verdictStyle(verdict: string): string {
  if (verdict.startsWith('STRONG')) return 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30';
  if (verdict.startsWith('MODERATE')) return 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30';
  if (verdict.startsWith('TOO_FAST')) return 'bg-orange-500/20 text-orange-400 border-orange-500/30';
  if (verdict.startsWith('NOT_COPYABLE')) return 'bg-red-500/20 text-red-400 border-red-500/30';
  if (verdict.startsWith('AVOID')) return 'bg-red-700/20 text-red-500 border-red-700/30';
  return 'bg-white/5 text-gray-400 border-white/10';
}

function verdictKey(verdict: string): string {
  return verdict.split(':')[0].trim();
}

function ScoreBar({ value, color = 'bg-blue-500' }: { value: number; color?: string }) {
  const pct = Math.max(0, Math.min(1, value)) * 100;
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-16 shrink-0 rounded-full bg-white/10">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="tabular-nums text-xs text-gray-300">{pct.toFixed(0)}</span>
    </div>
  );
}

function DecayBars({ decay }: { decay: AlphaEntry['latency_decay'] }) {
  const steps: [keyof AlphaEntry['latency_decay'], string][] = [
    ['200ms', '200'],
    ['500ms', '500'],
    ['1000ms', '1s'],
    ['1500ms', '1.5s'],
  ];
  return (
    <div className="flex items-end gap-1" title="Edge remaining at each latency threshold">
      {steps.map(([key, label]) => {
        const pct = Math.max(0, Math.min(1, decay[key])) * 100;
        const color = pct > 60 ? 'bg-emerald-500' : pct > 30 ? 'bg-amber-500' : 'bg-red-500';
        const h = Math.max(3, pct * 0.24);
        return (
          <div key={key} className="flex flex-col items-center gap-0.5">
            <div className={`w-3 rounded-t ${color}`} style={{ height: `${h}px` }} />
            <span className="text-[8px] text-gray-600">{label}</span>
          </div>
        );
      })}
    </div>
  );
}

function VerdictBadge({ verdict }: { verdict: string }) {
  const key = verdictKey(verdict);
  const reason = VERDICT_REASONS[key] ?? verdict;
  return (
    <div className="group relative inline-block">
      <span className={`cursor-help rounded border px-2 py-0.5 text-[10px] font-medium whitespace-nowrap ${verdictStyle(verdict)}`}>
        {key.replace(/_/g, ' ')}
      </span>
      <div className="pointer-events-none absolute bottom-full left-0 z-50 mb-1 hidden w-52 rounded-lg border border-white/10 bg-gray-950 p-2 text-[10px] text-gray-300 shadow-xl group-hover:block">
        {reason}
      </div>
    </div>
  );
}

/* ─── Wallet Detail Drawer ───────────────────────────────── */
// Alpha intelligence types for the drawer
interface WalletAlphaDetail {
  copyable_alpha_score: number;
  recommendation: 'copy_now' | 'monitor' | 'avoid';
  alpha_decay_risk: 'low' | 'medium' | 'high' | 'critical';
  decay_signal: string;
  factors: {
    timing_score: number;
    persistence_score: number;
    vol_adjusted_roi: number;
    latency_survivability: number;
    drawdown_stability: number;
    information_impact: number;
    suspiciousness_penalty: number;
  };
  timing_detail: { favourable_entry_ratio: number; median_detection_lag_ms: number | null; lag_score: number };
  influence_detail: { mean_price_deviation: number; conviction_trade_ratio: number; trade_count_used: number };
}

interface WalletDecayDetail {
  decay_alert: boolean;
  decay_magnitude: number | null;
  reason: string;
  windows: Record<string, { trade_count: number; profit_factor: number | null; win_rate: number }>;
}

interface WalletPerformance {
  live_status: {
    last_trade_at: string | null;
    trades_24h: number;
    activity_label: 'ACTIVE' | 'WARM' | 'DORMANT' | 'UNKNOWN';
  };
  copy_performance: {
    copied_trade_count: number;
    realized_pnl: number;
    win_rate: number | null;
    avg_pnl: number | null;
    profit_factor: number;
    last_copied_at: string | null;
    strategy_breakdown: { strategy: string; trade_count: number; realized_pnl: number; win_rate: number | null; avg_pnl: number | null; profit_factor: number }[];
    recent_closed: { opened_at: string | null; pnl: number; strategy: string }[];
  };
  category_performance: {
    category: string;
    trade_count: number;
    total_notional: number;
    est_win_rate: number | null;
    copied_trade_count: number;
    copied_realized_pnl: number | null;
    copied_win_rate: number | null;
    copied_profit_factor: number | null;
  }[];
  influence_summary: {
    is_leader?: boolean;
    is_follower?: boolean;
    leader_count: number;
    follower_count: number;
    influence_score?: number;
    top_influenced: { wallet_id: string; weight: number; mean_lag_s: number }[];
    top_influencing: { wallet_id: string; weight: number; mean_lag_s: number }[];
  };
}

function WalletDrawer({
  entry,
  onClose,
}: {
  entry: AlphaEntry | null;
  onClose: () => void;
}) {
  const [detail, setDetail] = useState<WalletDetail | null>(null);
  const [trades, setTrades] = useState<WalletTrade[]>([]);
  const [loading, setLoading] = useState(false);
  const [alphaDetail, setAlphaDetail] = useState<WalletAlphaDetail | null>(null);
  const [decayDetail, setDecayDetail] = useState<WalletDecayDetail | null>(null);
  const [perf, setPerf] = useState<WalletPerformance | null>(null);
  const drawerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!entry) return;
    setLoading(true);
    setDetail(null);
    setTrades([]);
    setAlphaDetail(null);
    setDecayDetail(null);
    setPerf(null);
    Promise.all([
      apiFetch<WalletDetail>(`/api/wallets/${entry.wallet_id}`),
      apiFetch<{ trades: WalletTrade[] }>(`/api/wallets/${entry.wallet_id}/trades?limit=50`),
      apiFetch<WalletAlphaDetail>(`/api/wallets/${entry.wallet_id}/alpha`).catch(() => null),
      apiFetch<WalletDecayDetail>(`/api/wallets/${entry.wallet_id}/alpha-decay`).catch(() => null),
      apiFetch<WalletPerformance>(`/api/wallets/${entry.wallet_id}/performance`).catch(() => null),
    ])
      .then(([d, t, alpha, decay, p]) => {
        setDetail(d);
        setTrades(t.trades ?? []);
        if (alpha) setAlphaDetail(alpha);
        if (decay) setDecayDetail(decay);
        if (p) setPerf(p);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [entry?.wallet_id]);

  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [onClose]);

  if (!entry) return null;

  const decay = entry.latency_decay;
  const decaySteps: [keyof typeof decay, string][] = [
    ['200ms', '200 ms'],
    ['500ms', '500 ms'],
    ['1000ms', '1 000 ms'],
    ['1500ms', '1 500 ms'],
  ];

  return (
    <div
      className="fixed inset-0 z-50 flex items-stretch justify-end"
      style={{ background: 'rgba(0,0,0,0.6)' }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div
        ref={drawerRef}
        className="flex h-full w-full max-w-2xl flex-col overflow-hidden border-l border-white/10 bg-gray-950 shadow-2xl"
        style={{ animation: 'slideIn 0.2s ease-out' }}
      >
        {/* Header */}
        <div className="flex items-start justify-between border-b border-white/10 p-5">
          <div>
            <div className="flex items-center gap-2">
              <h2 className="text-lg font-bold">{entry.label || entry.address.slice(0, 14) + '…'}</h2>
              {entry.is_real
                ? <span className="rounded bg-emerald-500/10 px-1.5 py-0 text-[10px] font-medium text-emerald-400">LIVE</span>
                : <span className="rounded bg-white/5 px-1.5 py-0 text-[10px] text-gray-500">DEMO</span>
              }
              <VerdictBadge verdict={entry.verdict} />
            </div>
            <p className="mt-0.5 font-mono text-xs text-gray-500">{entry.address}</p>
          </div>
          <button
            onClick={onClose}
            className="rounded-lg border border-white/10 px-3 py-1.5 text-sm text-gray-400 hover:bg-white/5 hover:text-white"
          >
            ✕ Close
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-5 space-y-6">
          {loading && (
            <div className="flex items-center gap-2 text-sm text-gray-500">
              <span className="h-2 w-2 animate-pulse rounded-full bg-blue-400" />
              Loading wallet data...
            </div>
          )}

          {/* ── WOULD I COPY THIS? decision card ── */}
          {(() => {
            const vKey = verdictKey(entry.verdict);
            let decision: 'copy' | 'monitor' | 'avoid';
            let decisionLabel: string;
            let decisionReason: string;
            let cardStyle: string;
            let iconEl: string;

            if (vKey === 'STRONG_CANDIDATE') {
              decision = 'copy';
              decisionLabel = 'Copy Now';
              decisionReason = 'High copyability + positive ROI. Edge survives realistic latency. Actively include in copy strategy.';
              cardStyle = 'border-emerald-500/40 bg-emerald-500/5';
              iconEl = '\u2714';
            } else if (vKey === 'MODERATE') {
              decision = 'monitor';
              decisionLabel = 'Monitor Only';
              decisionReason = 'Borderline metrics. Worth watching for improvement. Do not allocate full copy size yet.';
              cardStyle = 'border-yellow-500/30 bg-yellow-500/5';
              iconEl = '~';
            } else if (vKey === 'TOO_FAST') {
              decision = 'avoid';
              decisionLabel = 'Do Not Copy';
              decisionReason = 'Edge collapses by 500ms. Copyable only with sub-200ms infrastructure you likely do not have.';
              cardStyle = 'border-orange-500/30 bg-orange-500/5';
              iconEl = '\u26a0';
            } else if (vKey === 'NOT_COPYABLE' || vKey === 'AVOID') {
              decision = 'avoid';
              decisionLabel = 'Do Not Copy';
              decisionReason = entry.verdict.includes(':') ? entry.verdict.split(':')[1].trim() : 'Edge or copyability insufficient.';
              cardStyle = 'border-red-500/30 bg-red-500/5';
              iconEl = '\u2717';
            } else {
              decision = 'monitor';
              decisionLabel = 'Monitor Only';
              decisionReason = 'Insufficient data or weak signal. Collect more trades before deciding.';
              cardStyle = 'border-white/10 bg-white/[0.02]';
              iconEl = '?';
            }

            const decisionColor = decision === 'copy' ? 'text-emerald-400' : decision === 'monitor' ? 'text-yellow-400' : 'text-red-400';

            return (
              <div className={`rounded-xl border p-4 ${cardStyle}`}>
                <div className="flex items-start justify-between">
                  <div>
                    <p className="text-[10px] uppercase tracking-widest text-gray-500 mb-1">Would I copy this wallet?</p>
                    <p className={`text-xl font-black ${decisionColor}`}>{iconEl} {decisionLabel}</p>
                    <p className="mt-1.5 text-[11px] text-gray-400 max-w-sm">{decisionReason}</p>
                  </div>
                  <div className="text-right shrink-0">
                    <p className="text-[10px] text-gray-600">Copyable Alpha</p>
                    <p className={`text-lg font-bold tabular-nums ${entry.copyable_alpha > 0.05 ? 'text-emerald-400' : entry.copyable_alpha < 0 ? 'text-red-400' : 'text-gray-400'}`}>
                      {entry.copyable_alpha > 0 ? '+' : ''}{entry.copyable_alpha.toFixed(4)}
                    </p>
                  </div>
                </div>

                {/* Copyability sub-scorecard */}
                <div className="mt-3 grid grid-cols-2 gap-x-6 gap-y-1.5 border-t border-white/5 pt-3">
                  {[
                    {
                      label: 'Decay at 200ms',
                      value: entry.latency_decay['200ms'],
                      ok: entry.latency_decay['200ms'] > 0.6,
                      fmt: (v: number) => `${(v * 100).toFixed(0)}%`,
                    },
                    {
                      label: 'Decay at 500ms',
                      value: entry.latency_decay['500ms'],
                      ok: entry.latency_decay['500ms'] > 0.4,
                      fmt: (v: number) => `${(v * 100).toFixed(0)}%`,
                    },
                    {
                      label: 'Max Drawdown',
                      value: entry.max_drawdown,
                      ok: entry.max_drawdown < 0.3,
                      fmt: (v: number) => v > 0 ? `-${(v * 100).toFixed(0)}%` : '0%',
                    },
                    {
                      label: 'Consistency',
                      value: entry.consistency,
                      ok: entry.consistency > 0.5,
                      fmt: (v: number) => `${(v * 100).toFixed(0)}%`,
                    },
                    {
                      label: 'Suspiciousness',
                      value: entry.suspiciousness,
                      ok: entry.suspiciousness < 0.3,
                      fmt: (v: number) => `${(v * 100).toFixed(0)}%`,
                    },
                    {
                      label: 'Hit Rate',
                      value: entry.hit_rate,
                      ok: entry.hit_rate > 0.55,
                      fmt: (v: number) => `${(v * 100).toFixed(0)}%`,
                    },
                  ].map(({ label, value, ok, fmt }) => (
                    <div key={label} className="flex items-center justify-between text-xs">
                      <div className="flex items-center gap-1.5">
                        <span className={ok ? 'text-emerald-400 text-[10px]' : 'text-red-400 text-[10px]'}>{ok ? '\u2714' : '\u2717'}</span>
                        <span className="text-gray-500">{label}</span>
                      </div>
                      <span className={`tabular-nums font-medium ${ok ? 'text-gray-200' : 'text-red-400'}`}>{fmt(value)}</span>
                    </div>
                  ))}
                </div>
              </div>
            );
          })()}

          {/* Metrics Grid */}
          <div className="grid grid-cols-3 gap-3">
            {[
              { label: 'ROI', value: fmtPct(entry.roi), color: entry.roi > 0 ? 'text-emerald-400' : 'text-red-400' },
              { label: 'Hit Rate', value: fmtPct(entry.hit_rate), color: 'text-gray-200' },
              { label: 'Max Drawdown', value: entry.max_drawdown > 0 ? `-${(entry.max_drawdown * 100).toFixed(1)}%` : '—', color: entry.max_drawdown > 0.5 ? 'text-red-400' : entry.max_drawdown > 0.2 ? 'text-amber-400' : 'text-gray-400' },
              { label: 'Copyability', value: (entry.copyability_score * 100).toFixed(0), color: 'text-blue-400' },
              { label: 'Composite', value: (entry.composite_score * 100).toFixed(0), color: 'text-purple-400' },
              { label: 'Copyable Alpha', value: entry.copyable_alpha > 0 ? `+${entry.copyable_alpha.toFixed(4)}` : entry.copyable_alpha.toFixed(4), color: entry.copyable_alpha > 0.05 ? 'text-emerald-400' : 'text-gray-400' },
            ].map(({ label, value, color }) => (
              <div key={label} className="rounded-lg border border-white/5 bg-white/[0.02] p-3">
                <p className="text-[10px] uppercase tracking-wide text-gray-500">{label}</p>
                <p className={`mt-1 text-xl font-bold tabular-nums ${color}`}>{value}</p>
              </div>
            ))}
          </div>

          {/* Latency Decay Curve */}
          <div className="rounded-lg border border-white/5 bg-white/[0.02] p-4">
            <h3 className="mb-3 text-xs font-semibold uppercase tracking-wide text-gray-400">Latency Decay — Edge Survivability</h3>
            <p className="mb-3 text-[10px] text-gray-600">How much edge remains if you enter at each delay threshold. Below 30% = not copyable at that latency.</p>
            <div className="space-y-2">
              {decaySteps.map(([key, label]) => {
                const pct = Math.max(0, Math.min(1, decay[key])) * 100;
                const barColor = pct > 60 ? 'bg-emerald-500' : pct > 30 ? 'bg-amber-500' : 'bg-red-500';
                const textColor = pct > 60 ? 'text-emerald-400' : pct > 30 ? 'text-amber-400' : 'text-red-400';
                return (
                  <div key={key} className="flex items-center gap-3">
                    <span className="w-16 text-xs text-gray-500 tabular-nums">{label}</span>
                    <div className="flex-1 h-2 rounded-full bg-white/10">
                      <div className={`h-full rounded-full ${barColor}`} style={{ width: `${pct}%`, transition: 'width 0.4s' }} />
                    </div>
                    <span className={`w-12 text-right text-xs tabular-nums font-semibold ${textColor}`}>
                      {pct.toFixed(0)}%
                    </span>
                  </div>
                );
              })}
            </div>
          </div>

          {/* Score Breakdown */}
          {detail?.score?.explanation && Object.keys(detail.score.explanation).length > 0 && (
            <div className="rounded-lg border border-white/5 bg-white/[0.02] p-4">
              <h3 className="mb-3 text-xs font-semibold uppercase tracking-wide text-gray-400">Score Explanation</h3>
              <div className="grid grid-cols-2 gap-x-6 gap-y-1.5">
                {Object.entries(detail.score.explanation).map(([k, v]) => (
                  <div key={k} className="flex items-center justify-between">
                    <span className="text-[11px] text-gray-500 capitalize">{k.replace(/_/g, ' ')}</span>
                    <span className="text-[11px] font-medium text-gray-300 tabular-nums">
                      {typeof v === 'number' ? v.toFixed(3) : String(v)}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* ── Copyable Alpha Intelligence (6-factor breakdown) ── */}
          {alphaDetail && (
            <div className="rounded-lg border border-violet-500/20 bg-violet-500/5 p-4">
              <div className="mb-3 flex items-center justify-between">
                <h3 className="text-xs font-semibold uppercase tracking-wide text-violet-400">
                  Copyable Alpha Intelligence
                </h3>
                <div className="flex items-center gap-2">
                  <span className={`rounded px-2 py-0.5 text-[9px] font-bold uppercase ${
                    alphaDetail.recommendation === 'copy_now' ? 'bg-emerald-500/10 text-emerald-400' :
                    alphaDetail.recommendation === 'monitor'  ? 'bg-yellow-500/10 text-yellow-400' :
                    'bg-red-500/10 text-red-400'
                  }`}>
                    {alphaDetail.recommendation.replace('_', ' ')}
                  </span>
                  <span className={`text-[9px] font-bold ${
                    alphaDetail.alpha_decay_risk === 'low'    ? 'text-emerald-400' :
                    alphaDetail.alpha_decay_risk === 'medium' ? 'text-yellow-400' :
                    alphaDetail.alpha_decay_risk === 'high'   ? 'text-orange-400' : 'text-red-400'
                  }`}>decay: {alphaDetail.alpha_decay_risk}</span>
                </div>
              </div>

              {/* Score dial */}
              <div className="mb-4 flex items-center gap-4">
                <div className="flex flex-col items-center">
                  <span className="text-3xl font-black text-white tabular-nums">
                    {(alphaDetail.copyable_alpha_score * 100).toFixed(1)}
                  </span>
                  <span className="text-[9px] text-gray-500 uppercase">Alpha Score</span>
                </div>
                <div className="flex-1">
                  <div className="h-2 w-full rounded-full bg-white/10">
                    <div
                      className={`h-full rounded-full transition-all ${
                        alphaDetail.copyable_alpha_score >= 0.55 ? 'bg-emerald-500' :
                        alphaDetail.copyable_alpha_score >= 0.35 ? 'bg-yellow-500' : 'bg-red-500'
                      }`}
                      style={{ width: `${alphaDetail.copyable_alpha_score * 100}%` }}
                    />
                  </div>
                  <p className="mt-1 text-[10px] text-gray-500">{alphaDetail.decay_signal}</p>
                </div>
              </div>

              {/* 6-factor breakdown */}
              <div className="space-y-1.5">
                {[
                  { key: 'timing_score',          label: 'Timing',              w: 22, desc: 'Trades before price moves' },
                  { key: 'persistence_score',     label: 'Persistence',         w: 18, desc: 'Alpha consistent over time' },
                  { key: 'vol_adjusted_roi',      label: 'Vol-Adj ROI',         w: 20, desc: 'Sharpe-like return quality' },
                  { key: 'latency_survivability', label: 'Latency Survival',    w: 18, desc: 'Edge at 800ms copy lag' },
                  { key: 'drawdown_stability',    label: 'DD Stability',        w: 12, desc: 'No catastrophic drawdowns' },
                  { key: 'information_impact',    label: 'Info Impact',         w: 10, desc: 'Trades measurably move price' },
                ].map(({ key, label, w, desc }) => {
                  const val = alphaDetail.factors[key as keyof typeof alphaDetail.factors] as number;
                  const contribution = val * (w / 100);
                  const barColor = val >= 0.6 ? 'bg-emerald-500' : val >= 0.35 ? 'bg-yellow-500' : 'bg-red-500';
                  return (
                    <div key={key} className="grid grid-cols-[100px_1fr_32px_40px] items-center gap-2 text-[10px]">
                      <span className="text-gray-400 truncate" title={desc}>{label}</span>
                      <div className="h-1.5 rounded-full bg-white/10">
                        <div className={`h-full rounded-full ${barColor}`} style={{ width: `${val * 100}%` }} />
                      </div>
                      <span className="text-right tabular-nums text-gray-300">{(val * 100).toFixed(0)}</span>
                      <span className="text-right tabular-nums text-gray-600">×{w}%</span>
                    </div>
                  );
                })}
                {alphaDetail.factors.suspiciousness_penalty > 0 && (
                  <div className="grid grid-cols-[100px_1fr_32px_40px] items-center gap-2 text-[10px]">
                    <span className="text-red-400">Susp. Penalty</span>
                    <div className="h-1.5 rounded-full bg-white/10">
                      <div className="h-full rounded-full bg-red-500" style={{ width: `${alphaDetail.factors.suspiciousness_penalty * 100 * 6.7}%` }} />
                    </div>
                    <span className="text-right tabular-nums text-red-400">-{(alphaDetail.factors.suspiciousness_penalty * 100).toFixed(0)}</span>
                    <span className="text-right tabular-nums text-gray-600"></span>
                  </div>
                )}
              </div>

              {/* Sub-details */}
              <div className="mt-3 grid grid-cols-2 gap-3 border-t border-white/10 pt-3 text-[10px]">
                <div>
                  <p className="text-gray-600 mb-1">Timing Detail</p>
                  <p className="text-gray-400">Favourable entries: <span className="text-white">{(alphaDetail.timing_detail.favourable_entry_ratio * 100).toFixed(0)}%</span></p>
                  <p className="text-gray-400">Median lag: <span className="text-white">{alphaDetail.timing_detail.median_detection_lag_ms != null ? `${alphaDetail.timing_detail.median_detection_lag_ms.toFixed(0)} ms` : '—'}</span></p>
                </div>
                <div>
                  <p className="text-gray-600 mb-1">Information Impact</p>
                  <p className="text-gray-400">Price deviation: <span className="text-white">{(alphaDetail.influence_detail.mean_price_deviation * 100).toFixed(1)}%</span></p>
                  <p className="text-gray-400">Conviction trades: <span className="text-white">{(alphaDetail.influence_detail.conviction_trade_ratio * 100).toFixed(0)}%</span></p>
                </div>
              </div>
            </div>
          )}

          {/* ── Alpha Decay Rolling Window Analysis ── */}
          {decayDetail && (
            <div className={`rounded-lg border p-4 ${decayDetail.decay_alert ? 'border-red-500/30 bg-red-500/5' : 'border-white/5 bg-white/[0.02]'}`}>
              <div className="mb-3 flex items-center justify-between">
                <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-400">Alpha Decay Monitor</h3>
                {decayDetail.decay_alert && (
                  <span className="rounded bg-red-500/15 px-2 py-0.5 text-[9px] font-bold text-red-400">DECAY ALERT</span>
                )}
              </div>

              {decayDetail.decay_alert && (
                <p className="mb-3 text-[10px] text-red-400">{decayDetail.reason}</p>
              )}

              <div className="grid grid-cols-3 gap-2">
                {Object.entries(decayDetail.windows).map(([window, stats]) => {
                  const pf = stats.profit_factor;
                  const pfColor = pf == null ? 'text-gray-500' : pf >= 1.3 ? 'text-emerald-400' : pf >= 1.0 ? 'text-yellow-400' : 'text-red-400';
                  return (
                    <div key={window} className="rounded border border-white/5 p-2 text-center">
                      <p className="text-[9px] text-gray-500">{window.replace('last_', 'Last ')}</p>
                      <p className={`text-base font-bold tabular-nums ${pfColor}`}>
                        {pf != null ? pf.toFixed(2) : '—'}
                      </p>
                      <p className="text-[9px] text-gray-600">PF ({stats.trade_count} tr)</p>
                    </div>
                  );
                })}
              </div>

              {decayDetail.decay_magnitude != null && (
                <p className="mt-2 text-[10px] text-gray-500">
                  Decay magnitude: <span className={decayDetail.decay_magnitude > 0.5 ? 'text-red-400' : 'text-gray-300'}>{decayDetail.decay_magnitude.toFixed(2)} PF units</span>
                  {decayDetail.decay_magnitude > 0.5 ? ' — Consider reducing copy allocation.' : ''}
                </p>
              )}
            </div>
          )}

          {/* Additional Flags */}
          <div className="grid grid-cols-2 gap-3">
            <div className="rounded-lg border border-white/5 bg-white/[0.02] p-3">
              <p className="text-[10px] uppercase tracking-wide text-gray-500">Classification</p>
              <span className={`mt-1.5 inline-block rounded px-2 py-0.5 text-[10px] font-medium ${CLASSIFICATION_COLORS[entry.classification?.toLowerCase()] ?? CLASSIFICATION_COLORS.unknown}`}>
                {entry.classification || 'unknown'}
              </span>
            </div>
            <div className="rounded-lg border border-white/5 bg-white/[0.02] p-3">
              <p className="text-[10px] uppercase tracking-wide text-gray-500">Consistency / Suspiciousness</p>
              <p className="mt-1 text-sm tabular-nums text-gray-300">
                {(entry.consistency * 100).toFixed(0)}<span className="text-gray-600 text-[10px]">%</span>
                {' / '}
                <span className={entry.suspiciousness > 0.5 ? 'text-red-400' : 'text-gray-300'}>
                  {(entry.suspiciousness * 100).toFixed(0)}<span className="text-gray-600 text-[10px]">%</span>
                </span>
              </p>
            </div>
          </div>

          {/* ── REALIZED COPY PERFORMANCE ── */}
          {perf && (
            <div className="rounded-lg border border-white/5 bg-white/[0.02] p-4 space-y-4">
              {/* Live status badge */}
              <div className="flex items-center justify-between">
                <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-400">Realized Copy Performance</h3>
                <span className={`rounded px-2 py-0.5 text-[9px] font-bold uppercase ${
                  perf.live_status.activity_label === 'ACTIVE'  ? 'bg-emerald-500/15 text-emerald-400' :
                  perf.live_status.activity_label === 'WARM'    ? 'bg-yellow-500/15 text-yellow-400' :
                  perf.live_status.activity_label === 'DORMANT' ? 'bg-red-500/10 text-red-400' :
                  'bg-white/5 text-gray-500'
                }`}>
                  {perf.live_status.activity_label}
                  {perf.live_status.last_trade_at && (
                    <span className="ml-1 font-normal normal-case text-gray-500">
                      · {new Date(perf.live_status.last_trade_at).toLocaleDateString('tr-TR', { timeZone: 'Europe/Istanbul', day: 'numeric', month: 'short' })}
                    </span>
                  )}
                </span>
              </div>

              {/* Predictive vs Realized comparison */}
              <div className="grid grid-cols-2 gap-3">
                <div className="rounded border border-white/5 bg-white/[0.02] p-3">
                  <p className="text-[9px] uppercase tracking-wide text-gray-600 mb-1">Predictive Score</p>
                  <p className="text-lg font-bold text-purple-400">{(entry.composite_score * 100).toFixed(0)}</p>
                  <p className="text-[10px] text-gray-600">composite score</p>
                </div>
                <div className={`rounded border p-3 ${perf.copy_performance.profit_factor >= 1.3 ? 'border-emerald-500/20 bg-emerald-500/5' : perf.copy_performance.copied_trade_count === 0 ? 'border-white/5 bg-white/[0.02]' : 'border-red-500/15 bg-red-500/5'}`}>
                  <p className="text-[9px] uppercase tracking-wide text-gray-600 mb-1">Realized PF</p>
                  <p className={`text-lg font-bold tabular-nums ${perf.copy_performance.copied_trade_count === 0 ? 'text-gray-500' : perf.copy_performance.profit_factor >= 1.3 ? 'text-emerald-400' : perf.copy_performance.profit_factor >= 1.0 ? 'text-yellow-400' : 'text-red-400'}`}>
                    {perf.copy_performance.copied_trade_count === 0 ? '—' : perf.copy_performance.profit_factor.toFixed(2)}
                  </p>
                  <p className="text-[10px] text-gray-600">{perf.copy_performance.copied_trade_count} copied trades</p>
                </div>
              </div>

              {perf.copy_performance.copied_trade_count > 0 && (
                <>
                  <div className="grid grid-cols-3 gap-2 text-xs">
                    {[
                      { label: 'Total PnL', value: `${perf.copy_performance.realized_pnl >= 0 ? '+' : ''}$${perf.copy_performance.realized_pnl.toFixed(2)}`, color: perf.copy_performance.realized_pnl >= 0 ? 'text-emerald-400' : 'text-red-400' },
                      { label: 'Win Rate', value: perf.copy_performance.win_rate != null ? `${(perf.copy_performance.win_rate * 100).toFixed(0)}%` : '—', color: (perf.copy_performance.win_rate ?? 0) >= 0.55 ? 'text-emerald-400' : 'text-gray-300' },
                      { label: 'Avg PnL/Trade', value: perf.copy_performance.avg_pnl != null ? `$${perf.copy_performance.avg_pnl.toFixed(3)}` : '—', color: (perf.copy_performance.avg_pnl ?? 0) > 0 ? 'text-emerald-400' : 'text-red-400' },
                    ].map(({ label, value, color }) => (
                      <div key={label} className="rounded border border-white/5 bg-white/[0.02] p-2 text-center">
                        <p className="text-[9px] text-gray-600">{label}</p>
                        <p className={`font-semibold tabular-nums ${color}`}>{value}</p>
                      </div>
                    ))}
                  </div>

                  {/* Strategy breakdown */}
                  {perf.copy_performance.strategy_breakdown.length > 0 && (
                    <div>
                      <p className="text-[10px] text-gray-500 mb-1.5">By Strategy</p>
                      <div className="space-y-1">
                        {perf.copy_performance.strategy_breakdown.map((s) => (
                          <div key={s.strategy} className="flex items-center gap-2 text-[10px]">
                            <span className="w-28 truncate text-gray-400">{s.strategy}</span>
                            <div className="flex-1 h-1.5 rounded-full bg-white/10">
                              <div className={`h-full rounded-full ${s.profit_factor >= 1.3 ? 'bg-emerald-500' : s.profit_factor >= 1.0 ? 'bg-yellow-500' : 'bg-red-500'}`}
                                style={{ width: `${Math.min(100, (s.profit_factor / 3) * 100)}%` }} />
                            </div>
                            <span className="w-10 text-right tabular-nums text-gray-300">PF {s.profit_factor.toFixed(2)}</span>
                            <span className="text-gray-600">({s.trade_count})</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Recent PnL sparkline */}
                  {perf.copy_performance.recent_closed.length > 0 && (
                    <div>
                      <p className="text-[10px] text-gray-500 mb-1">Recent Closed (last {perf.copy_performance.recent_closed.length})</p>
                      <div className="flex items-end gap-1">
                        {perf.copy_performance.recent_closed.map((t, i) => {
                          const max = Math.max(...perf.copy_performance.recent_closed.map((x) => Math.abs(x.pnl)), 0.001);
                          const h = Math.max(3, (Math.abs(t.pnl) / max) * 28);
                          return (
                            <div key={i} title={`$${t.pnl.toFixed(4)}`}
                              className={`w-3 rounded-t flex-shrink-0 ${t.pnl > 0 ? 'bg-emerald-500' : t.pnl < 0 ? 'bg-red-500' : 'bg-gray-600'}`}
                              style={{ height: `${h}px` }} />
                          );
                        })}
                      </div>
                    </div>
                  )}
                </>
              )}

              {perf.copy_performance.copied_trade_count === 0 && (
                <p className="text-[11px] text-gray-600">No closed copy trades yet. System has not generated paper positions from this wallet.</p>
              )}
            </div>
          )}

          {/* ── CATEGORY PERFORMANCE ── */}
          {perf && perf.category_performance.length > 0 && (
            <div className="rounded-lg border border-white/5 bg-white/[0.02] p-4">
              <h3 className="mb-3 text-xs font-semibold uppercase tracking-wide text-gray-400">Category Performance</h3>
              <p className="mb-2 text-[10px] text-gray-600">Wallet activity per category + realized copy PnL where available.</p>
              <div className="overflow-x-auto">
                <table className="w-full text-[10px]">
                  <thead>
                    <tr className="border-b border-white/5 text-left text-gray-600">
                      <th className="py-1.5 pr-3">Category</th>
                      <th className="py-1.5 pr-3 text-right">Trades</th>
                      <th className="py-1.5 pr-3 text-right">Est WR</th>
                      <th className="py-1.5 pr-3 text-right">Copied</th>
                      <th className="py-1.5 pr-3 text-right">Copy PnL</th>
                      <th className="py-1.5 text-right">Copy PF</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-white/[0.04]">
                    {perf.category_performance.slice(0, 8).map((cat) => {
                      const hasCopy = cat.copied_trade_count > 0;
                      const pnlColor = !hasCopy ? 'text-gray-600' :
                        (cat.copied_realized_pnl ?? 0) > 0 ? 'text-emerald-400' : 'text-red-400';
                      const pfColor = !hasCopy || cat.copied_profit_factor == null ? 'text-gray-600' :
                        cat.copied_profit_factor >= 1.3 ? 'text-emerald-400' :
                        cat.copied_profit_factor >= 1.0 ? 'text-yellow-400' : 'text-red-400';
                      return (
                        <tr key={cat.category} className="hover:bg-white/[0.02]">
                          <td className="py-1.5 pr-3 capitalize text-gray-400">{cat.category}</td>
                          <td className="py-1.5 pr-3 text-right tabular-nums text-gray-500">{cat.trade_count}</td>
                          <td className="py-1.5 pr-3 text-right tabular-nums text-gray-400">
                            {cat.est_win_rate != null ? `${(cat.est_win_rate * 100).toFixed(0)}%` : '—'}
                          </td>
                          <td className="py-1.5 pr-3 text-right tabular-nums text-gray-500">
                            {hasCopy ? cat.copied_trade_count : '—'}
                          </td>
                          <td className={`py-1.5 pr-3 text-right tabular-nums font-medium ${pnlColor}`}>
                            {hasCopy && cat.copied_realized_pnl != null
                              ? `${cat.copied_realized_pnl >= 0 ? '+' : ''}$${cat.copied_realized_pnl.toFixed(3)}`
                              : '—'}
                          </td>
                          <td className={`py-1.5 text-right tabular-nums font-bold ${pfColor}`}>
                            {hasCopy && cat.copied_profit_factor != null
                              ? cat.copied_profit_factor.toFixed(2)
                              : '—'}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* ── INFLUENCE SUMMARY ── */}
          {perf && (perf.influence_summary.leader_count > 0 || perf.influence_summary.follower_count > 0 || perf.influence_summary.is_leader) && (
            <div className="rounded-lg border border-white/5 bg-white/[0.02] p-4">
              <div className="mb-3 flex items-center justify-between">
                <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-400">Influence Network</h3>
                <div className="flex items-center gap-2">
                  {perf.influence_summary.is_leader && (
                    <span className="rounded bg-violet-500/15 px-2 py-0.5 text-[9px] font-bold text-violet-400">LEADER</span>
                  )}
                  {perf.influence_summary.is_follower && !perf.influence_summary.is_leader && (
                    <span className="rounded bg-orange-500/10 px-2 py-0.5 text-[9px] text-orange-400">FOLLOWER</span>
                  )}
                  {perf.influence_summary.influence_score != null && (
                    <span className="text-[9px] text-gray-500">score {perf.influence_summary.influence_score.toFixed(3)}</span>
                  )}
                </div>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div className="rounded border border-white/5 p-3">
                  <p className="text-[9px] uppercase text-gray-600 mb-1">Wallets Following This</p>
                  <p className="text-xl font-bold text-violet-400">{perf.influence_summary.leader_count}</p>
                  {perf.influence_summary.top_influenced.slice(0, 3).map((w) => (
                    <p key={w.wallet_id} className="text-[9px] text-gray-600 truncate">
                      → {w.wallet_id.slice(0, 10)}… <span className="text-gray-700">lag {w.mean_lag_s.toFixed(0)}s</span>
                    </p>
                  ))}
                </div>
                <div className="rounded border border-white/5 p-3">
                  <p className="text-[9px] uppercase text-gray-600 mb-1">This Wallet Follows</p>
                  <p className="text-xl font-bold text-orange-400">{perf.influence_summary.follower_count}</p>
                  {perf.influence_summary.top_influencing.slice(0, 3).map((w) => (
                    <p key={w.wallet_id} className="text-[9px] text-gray-600 truncate">
                      ← {w.wallet_id.slice(0, 10)}… <span className="text-gray-700">lag {w.mean_lag_s.toFixed(0)}s</span>
                    </p>
                  ))}
                </div>
              </div>
            </div>
          )}

          {/* Trade Timing Mini-Analysis */}
          {trades.length >= 2 && (() => {
            const lags = trades.map(t => t.detection_lag_ms).filter((l): l is number => l != null && l < 10_000_000);
            const avgLag = lags.length > 0 ? lags.reduce((a, b) => a + b, 0) / lags.length : null;
            const medLag = lags.length > 0 ? [...lags].sort((a, b) => a - b)[Math.floor(lags.length / 2)] : null;
            const buys = trades.filter(t => t.side === 'BUY').length;
            const sells = trades.filter(t => t.side === 'SELL').length;
            const totalNotional = trades.reduce((s, t) => s + (t.notional ?? 0), 0);
            const avgNotional = trades.length > 0 ? totalNotional / trades.length : 0;
            const highLagPct = lags.length > 0 ? (lags.filter(l => l > 1000).length / lags.length) * 100 : 0;

            return (
              <div className="rounded-lg border border-white/5 bg-white/[0.02] p-4">
                <h3 className="mb-3 text-xs font-semibold uppercase tracking-wide text-gray-400">
                  Trade Timing Analysis <span className="text-gray-600 normal-case font-normal">({trades.length} trades)</span>
                </h3>
                <div className="grid grid-cols-2 gap-x-8 gap-y-2 text-xs">
                  <div>
                    <span className="text-gray-500">Avg detection lag: </span>
                    <span className={`font-semibold tabular-nums ${avgLag == null ? 'text-gray-600' : avgLag > 1000 ? 'text-red-400' : avgLag > 400 ? 'text-amber-400' : 'text-emerald-400'}`}>
                      {avgLag != null ? `${Math.round(avgLag)} ms` : '--'}
                    </span>
                  </div>
                  <div>
                    <span className="text-gray-500">Median detection lag: </span>
                    <span className="font-semibold tabular-nums text-gray-300">
                      {medLag != null ? `${medLag} ms` : '--'}
                    </span>
                  </div>
                  <div>
                    <span className="text-gray-500">Buy / Sell split: </span>
                    <span className="font-medium text-gray-300">{buys} / {sells}</span>
                  </div>
                  <div>
                    <span className="text-gray-500">Avg notional: </span>
                    <span className="font-medium tabular-nums text-gray-300">${avgNotional.toFixed(2)}</span>
                  </div>
                  <div>
                    <span className="text-gray-500">High-lag trades (&gt;1s): </span>
                    <span className={`font-medium tabular-nums ${highLagPct > 50 ? 'text-red-400' : 'text-gray-300'}`}>
                      {highLagPct.toFixed(0)}%
                    </span>
                  </div>
                  <div>
                    <span className="text-gray-500">Total notional: </span>
                    <span className="font-medium tabular-nums text-gray-300">${totalNotional.toFixed(0)}</span>
                  </div>
                </div>
                {avgLag != null && avgLag > 1000 && (
                  <p className="mt-2 text-[10px] text-red-400/80">
                    Warning: avg lag &gt;1000ms -- this wallet's trades are detected late. Copy edge may be severely reduced.
                  </p>
                )}
              </div>
            );
          })()}

          {/* Trade History */}
          <div>
            <h3 className="mb-3 text-xs font-semibold uppercase tracking-wide text-gray-400">
              Recent Trades <span className="text-gray-600 normal-case">({trades.length} loaded)</span>
            </h3>
            {trades.length === 0 && !loading && (
              <p className="text-sm text-gray-600">No trades recorded yet for this wallet.</p>
            )}
            {trades.length > 0 && (
              <div className="overflow-x-auto rounded-lg border border-white/5">
                <table className="w-full text-xs">
                  <thead className="border-b border-white/5 bg-white/[0.02] text-left text-[10px] text-gray-500">
                    <tr>
                      <th className="px-3 py-2">Time</th>
                      <th className="px-3 py-2">Side</th>
                      <th className="px-3 py-2">Outcome</th>
                      <th className="px-3 py-2">Price</th>
                      <th className="px-3 py-2">Size</th>
                      <th className="px-3 py-2">Notional</th>
                      <th className="px-3 py-2">Lag</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-white/5">
                    {trades.map((t) => (
                      <tr key={t.id} className="hover:bg-white/[0.025]">
                        <td className="px-3 py-2 text-gray-500 tabular-nums whitespace-nowrap">
                          {t.occurred_at
                            ? new Date(t.occurred_at).toLocaleString('tr-TR', { timeZone: 'Europe/Istanbul', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })
                            : '—'}
                        </td>
                        <td className="px-3 py-2">
                          <span className={`rounded px-1.5 py-0 text-[9px] font-medium ${t.side === 'BUY' ? 'bg-emerald-500/15 text-emerald-400' : 'bg-red-500/15 text-red-400'}`}>
                            {t.side}
                          </span>
                        </td>
                        <td className="px-3 py-2 text-gray-300">{t.outcome ?? '—'}</td>
                        <td className="px-3 py-2 tabular-nums text-gray-300">{t.price != null ? t.price.toFixed(3) : '—'}</td>
                        <td className="px-3 py-2 tabular-nums text-gray-300">{t.size != null ? t.size.toFixed(2) : '—'}</td>
                        <td className="px-3 py-2 tabular-nums text-gray-300">
                          {t.notional != null ? `$${t.notional.toFixed(2)}` : '—'}
                        </td>
                        <td className={`px-3 py-2 tabular-nums ${(t.detection_lag_ms ?? 0) > 1000 ? 'text-amber-400' : 'text-gray-500'}`}>
                          {t.detection_lag_ms != null ? `${t.detection_lag_ms}ms` : '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      </div>

      <style>{`
        @keyframes slideIn {
          from { transform: translateX(100%); opacity: 0; }
          to   { transform: translateX(0);    opacity: 1; }
        }
      `}</style>
    </div>
  );
}

/* ─── Sorting / Filtering State ─────────────────────────── */
type SortKey = 'copyable_alpha' | 'roi' | 'copyability_score' | 'composite_score' | 'max_drawdown';
type SourceFilter = 'all' | 'live' | 'demo';
type ActivityFilter = 'all' | 'active' | 'warm_plus' | 'profitable_copied';

/* ─── Main ───────────────────────────────────────────────── */
export default function WalletsPage() {
  const [view, setView] = useState<'alpha' | 'all'>('alpha');
  const [alpha, setAlpha] = useState<AlphaEntry[]>([]);
  const [wallets, setWallets] = useState<WalletSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);
  const [sortKey, setSortKey] = useState<SortKey>('copyable_alpha');
  const [sortAsc, setSortAsc] = useState(false);
  const [sourceFilter, setSourceFilter] = useState<SourceFilter>('all');
  const [activityFilter, setActivityFilter] = useState<ActivityFilter>('all');
  const [selectedWallet, setSelectedWallet] = useState<AlphaEntry | null>(null);

  const fetchAll = useCallback(async () => {
    try {
      const [alphaData, allData] = await Promise.all([
        apiFetch<{ leaderboard: AlphaEntry[] }>('/api/wallets/alpha-leaderboard?limit=100'),
        apiFetch<{ wallets: WalletSummary[] }>('/api/wallets?limit=100'),
      ]);
      setAlpha(alphaData.leaderboard || []);
      setWallets(allData.wallets || []);
      setLastRefresh(new Date());
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchAll(); }, [fetchAll]);
  useEffect(() => {
    const id = setInterval(fetchAll, POLL_MS);
    return () => clearInterval(id);
  }, [fetchAll]);

  /* ─── Derived ─── */
  const filteredAlpha = alpha
    .filter((a) => {
      if (sourceFilter === 'live') return a.is_real;
      if (sourceFilter === 'demo') return !a.is_real;
      return true;
    })
    .filter((a) => {
      if (activityFilter === 'all') return true;
      const ww = walletMap[a.wallet_id];
      const label = ww?.live_status?.activity_label ?? 'UNKNOWN';
      if (activityFilter === 'active') return label === 'ACTIVE';
      if (activityFilter === 'warm_plus') return label === 'ACTIVE' || label === 'WARM';
      if (activityFilter === 'profitable_copied') {
        const pnl = ww?.copy_performance?.copied_realized_pnl ?? 0;
        const cnt = ww?.copy_performance?.copied_trade_count ?? 0;
        return cnt > 0 && pnl > 0;
      }
      return true;
    })
    .sort((a, b) => {
      const va = a[sortKey] as number;
      const vb = b[sortKey] as number;
      return sortAsc ? va - vb : vb - va;
    });

  const strongCount = alpha.filter((a) => a.verdict.startsWith('STRONG')).length;
  const moderateCount = alpha.filter((a) => a.verdict.startsWith('MODERATE')).length;
  const avoidCount = alpha.filter(
    (a) => a.verdict.startsWith('NOT_COPYABLE') || a.verdict.startsWith('TOO_FAST') || a.verdict.startsWith('AVOID'),
  ).length;
  const liveCount = wallets.filter((w) => !w.address.startsWith('0xdemo')).length;
  const scoredCount = wallets.filter((w) => w.score !== null).length;

  const walletMap = Object.fromEntries(wallets.map((w) => [w.id, w]));

  function toggleSort(key: SortKey) {
    if (sortKey === key) setSortAsc((p) => !p);
    else { setSortKey(key); setSortAsc(false); }
  }

  function SortTh({ k, children }: { k: SortKey; children: React.ReactNode }) {
    const active = sortKey === k;
    return (
      <th
        className={`px-4 py-3 cursor-pointer select-none whitespace-nowrap hover:text-gray-200 ${active ? 'text-blue-400' : ''}`}
        onClick={() => toggleSort(k)}
      >
        {children} {active ? (sortAsc ? '↑' : '↓') : <span className="text-gray-700">↕</span>}
      </th>
    );
  }

  return (
    <>
      {selectedWallet && (
        <WalletDrawer entry={selectedWallet} onClose={() => setSelectedWallet(null)} />
      )}

      <div className="space-y-5">
        {/* Header */}
        <div className="flex items-start justify-between">
          <div>
            <h1 className="text-xl font-bold">Wallet Intelligence</h1>
            <p className="text-sm text-gray-500">
              {view === 'alpha'
                ? 'Copyable Alpha Leaderboard — ROI × copyability, not just profit'
                : 'All tracked wallets with latest scores'}
            </p>
          </div>
          <div className="flex items-center gap-3">
            {lastRefresh && (
              <span className="text-xs text-gray-600">
                {loading
                  ? <span className="inline-flex items-center gap-1"><span className="h-1.5 w-1.5 animate-pulse rounded-full bg-blue-400" />updating…</span>
                  : <>updated {lastRefresh.toLocaleTimeString('tr-TR', { timeZone: 'Europe/Istanbul' })}</>
                }
              </span>
            )}
            <button
              onClick={fetchAll}
              disabled={loading}
              className="rounded-lg border border-white/10 bg-white/5 px-3 py-1.5 text-xs hover:bg-white/10 disabled:opacity-40"
            >
              ↺ Refresh
            </button>
          </div>
        </div>

        {/* Stats */}
        <div className="grid grid-cols-5 gap-3">
          {[
            { label: 'Tracked', value: wallets.length },
            { label: 'Live Wallets', value: liveCount, sub: 'from Polymarket' },
            { label: 'Scored', value: scoredCount },
            { label: 'Strong Candidates', value: strongCount, hot: strongCount > 0, color: 'text-emerald-400' },
            { label: 'Avoid / Too Fast', value: avoidCount, color: 'text-red-400' },
          ].map(({ label, value, sub, hot, color }) => (
            <div key={label} className={`rounded-xl border p-3 ${hot ? 'border-emerald-500/30 bg-emerald-500/5' : 'border-white/5 bg-surface-raised'}`}>
              <p className="text-[10px] uppercase tracking-wide text-gray-500">{label}</p>
              {sub && <p className="text-[9px] text-gray-600">{sub}</p>}
              <p className={`mt-1 text-2xl font-bold tabular-nums ${color ?? ''}`}>{value}</p>
            </div>
          ))}
        </div>

        {/* Tab + Filters row */}
        <div className="flex flex-wrap items-center gap-3">
          {/* Tab switcher */}
          <div className="flex gap-1 rounded-xl border border-white/5 bg-surface-raised p-1 w-fit">
            {(['alpha', 'all'] as const).map((t) => (
              <button
                key={t}
                onClick={() => setView(t)}
                className={`rounded-lg px-4 py-2 text-sm font-medium transition-colors ${view === t ? 'bg-white/10 text-white' : 'text-gray-500 hover:text-gray-300'}`}
              >
                {t === 'alpha' ? `Alpha Leaderboard (${alpha.length})` : `All Wallets (${wallets.length})`}
              </button>
            ))}
          </div>

          {/* Source filter */}
          {view === 'alpha' && (
            <div className="flex gap-1 rounded-xl border border-white/5 bg-surface-raised p-1 w-fit">
              {(['all', 'live', 'demo'] as const).map((f) => (
                <button
                  key={f}
                  onClick={() => setSourceFilter(f)}
                  className={`rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${sourceFilter === f ? 'bg-white/10 text-white' : 'text-gray-500 hover:text-gray-300'}`}
                >
                  {f === 'all' ? 'All' : f === 'live' ? '🟢 Live' : '⬜ Demo'}
                </button>
              ))}
            </div>
          )}

          {/* Activity filter */}
          {view === 'alpha' && (
            <div className="flex gap-1 rounded-xl border border-white/5 bg-surface-raised p-1 w-fit">
              {([
                { key: 'all', label: 'All' },
                { key: 'active', label: 'Active only' },
                { key: 'warm_plus', label: 'Active + Warm' },
                { key: 'profitable_copied', label: 'Profitable copied' },
              ] as const).map(({ key, label }) => (
                <button
                  key={key}
                  onClick={() => setActivityFilter(key)}
                  className={`rounded-lg px-3 py-1.5 text-xs font-medium transition-colors whitespace-nowrap ${activityFilter === key ? 'bg-white/10 text-white' : 'text-gray-500 hover:text-gray-300'}`}
                >
                  {label}
                </button>
              ))}
            </div>
          )}

          {/* Verdict count summary */}
          {view === 'alpha' && (
            <div className="flex items-center gap-2 text-[10px]">
              <span className="text-gray-600">Quick filter:</span>
              <span className="rounded bg-emerald-500/10 px-1.5 py-0.5 text-emerald-400">{strongCount} STRONG</span>
              <span className="rounded bg-yellow-500/10 px-1.5 py-0.5 text-yellow-400">{moderateCount} MODERATE</span>
              <span className="rounded bg-red-500/10 px-1.5 py-0.5 text-red-400">{avoidCount} AVOID</span>
            </div>
          )}
        </div>

        {loading && (
          <div className="flex items-center gap-2 text-sm text-gray-500">
            <span className="h-2 w-2 animate-pulse rounded-full bg-blue-400" />
            Loading wallet scores…
          </div>
        )}

        {/* ── ALPHA LEADERBOARD ── */}
        {view === 'alpha' && !loading && (
          <div className="overflow-x-auto rounded-xl border border-white/5">
            <table className="w-full text-sm">
              <thead className="border-b border-white/5 bg-surface-raised text-left text-xs text-gray-500">
                <tr>
                  <th className="px-4 py-3 w-8">#</th>
                  <th className="px-4 py-3">Wallet</th>
                  <th className="px-4 py-3">Type</th>
                  <SortTh k="roi">ROI</SortTh>
                  <th className="px-4 py-3">Hit Rate</th>
                  <SortTh k="copyability_score">Copyability</SortTh>
                  <SortTh k="composite_score">Composite</SortTh>
                  <SortTh k="copyable_alpha">Alpha Score</SortTh>
                  <th className="px-4 py-3">Latency Decay</th>
                  <SortTh k="max_drawdown">Max DD</SortTh>
                  <th className="px-4 py-3">Copied PnL</th>
                  <th className="px-4 py-3">Last Active</th>
                  <th className="px-4 py-3">Verdict</th>
                  <th className="px-4 py-3 w-16"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5">
                {filteredAlpha.map((a, idx) => (
                  <tr
                    key={a.wallet_id}
                    className="hover:bg-white/[0.03] transition-colors cursor-pointer"
                    onClick={() => setSelectedWallet(a)}
                  >
                    <td className="px-4 py-3 text-gray-500 tabular-nums">{idx + 1}</td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        <span className="font-medium">
                          {a.label || a.address.slice(0, 10) + '…'}
                        </span>
                        {a.is_real ? (
                          <span className="rounded bg-emerald-500/10 px-1.5 py-0 text-[9px] font-medium text-emerald-400">LIVE</span>
                        ) : (
                          <span className="rounded bg-white/5 px-1.5 py-0 text-[9px] text-gray-600">DEMO</span>
                        )}
                      </div>
                      <div className="font-mono text-[10px] text-gray-600">
                        {a.address.slice(0, 12)}…{a.address.slice(-4)}
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      <span className={`rounded px-2 py-0.5 text-[10px] font-medium ${CLASSIFICATION_COLORS[a.classification?.toLowerCase()] ?? CLASSIFICATION_COLORS.unknown}`}>
                        {a.classification || 'unknown'}
                      </span>
                    </td>
                    <td className={`px-4 py-3 tabular-nums font-semibold ${a.roi > 0.1 ? 'text-emerald-400' : a.roi > 0 ? 'text-emerald-300' : 'text-red-400'}`}>
                      {fmtPct(a.roi)}
                    </td>
                    <td className="px-4 py-3">
                      <ScoreBar value={a.hit_rate} color={a.hit_rate > 0.65 ? 'bg-emerald-500' : 'bg-amber-500'} />
                    </td>
                    <td className="px-4 py-3">
                      <ScoreBar value={a.copyability_score} color="bg-blue-500" />
                    </td>
                    <td className="px-4 py-3">
                      <ScoreBar value={a.composite_score} color="bg-purple-500" />
                    </td>
                    <td className="px-4 py-3">
                      <span className={`tabular-nums font-bold ${a.copyable_alpha > 0.05 ? 'text-emerald-400' : a.copyable_alpha < 0 ? 'text-red-400' : 'text-gray-400'}`}>
                        {a.copyable_alpha > 0 ? '+' : ''}{a.copyable_alpha.toFixed(4)}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      <DecayBars decay={a.latency_decay} />
                    </td>
                    <td className={`px-4 py-3 tabular-nums text-xs ${a.max_drawdown > 0.5 ? 'text-red-400' : a.max_drawdown > 0.2 ? 'text-amber-400' : 'text-gray-400'}`}>
                      {a.max_drawdown > 0 ? `-${(a.max_drawdown * 100).toFixed(1)}%` : '-'}
                    </td>
                    {/* Copied PnL */}
                    {(() => {
                      const ww = walletMap[a.wallet_id];
                      const cp = ww?.copy_performance;
                      const pnl = cp?.copied_realized_pnl ?? null;
                      const cnt = cp?.copied_trade_count ?? 0;
                      return (
                        <td className="px-4 py-3 text-xs tabular-nums">
                          {cnt === 0
                            ? <span className="text-gray-600">—</span>
                            : <span className={pnl != null && pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}>
                                {pnl != null ? `${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)}` : '—'}
                                <span className="text-gray-600 ml-1">({cnt})</span>
                              </span>
                          }
                        </td>
                      );
                    })()}
                    {/* Last Active */}
                    {(() => {
                      const ww = walletMap[a.wallet_id];
                      const ls = ww?.live_status;
                      const label = ls?.activity_label ?? 'UNKNOWN';
                      const lastAt = ls?.last_trade_at;
                      return (
                        <td className="px-4 py-3 text-xs">
                          <span className={`rounded px-1.5 py-0.5 text-[9px] font-bold ${
                            label === 'ACTIVE'  ? 'bg-emerald-500/15 text-emerald-400' :
                            label === 'WARM'    ? 'bg-yellow-500/10 text-yellow-400' :
                            label === 'DORMANT' ? 'bg-red-500/10 text-red-400' :
                            'bg-white/5 text-gray-600'
                          }`}>{label}</span>
                          {lastAt && (
                            <div className="text-[9px] text-gray-600 mt-0.5 tabular-nums">
                              {new Date(lastAt).toLocaleDateString('tr-TR', { timeZone: 'Europe/Istanbul', day: 'numeric', month: 'short' })}
                            </div>
                          )}
                        </td>
                      );
                    })()}
                    <td className="px-4 py-3" onClick={(e) => e.stopPropagation()}>
                      <VerdictBadge verdict={a.verdict} />
                    </td>
                    <td className="px-4 py-3 text-right">
                      <button
                        onClick={(e) => { e.stopPropagation(); setSelectedWallet(a); }}
                        className="rounded border border-white/10 px-2 py-1 text-[10px] text-gray-400 hover:bg-white/5 hover:text-white"
                      >
                        Detail →
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {filteredAlpha.length === 0 && (
              <div className="py-12 text-center text-sm text-gray-500">
                No scored wallets match the current filter.
              </div>
            )}
          </div>
        )}

        {/* ── ALL WALLETS ── */}
        {view === 'all' && !loading && (
          <div className="overflow-x-auto rounded-xl border border-white/5">
            <table className="w-full text-sm">
              <thead className="border-b border-white/5 bg-surface-raised text-left text-xs text-gray-500">
                <tr>
                  <th className="px-4 py-3 w-8">#</th>
                  <th className="px-4 py-3">Wallet</th>
                  <th className="px-4 py-3">Composite</th>
                  <th className="px-4 py-3">Copyability</th>
                  <th className="px-4 py-3">ROI</th>
                  <th className="px-4 py-3">Hit Rate</th>
                  <th className="px-4 py-3">Max DD</th>
                  <th className="px-4 py-3">Copied PnL</th>
                  <th className="px-4 py-3">Last Active</th>
                  <th className="px-4 py-3">Type</th>
                  <th className="px-4 py-3">Source</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5">
                {wallets.map((w, i) => (
                  <tr key={w.id} className="hover:bg-white/[0.025] transition-colors">
                    <td className="px-4 py-3 text-gray-500 tabular-nums">{i + 1}</td>
                    <td className="px-4 py-3">
                      <span className="font-medium">{w.label || w.address.slice(0, 12) + '…'}</span>
                      <div className="font-mono text-[10px] text-gray-600">
                        {w.address.slice(0, 12)}…{w.address.slice(-4)}
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      {w.score?.composite != null ? (
                        <ScoreBar value={w.score.composite} color="bg-purple-500" />
                      ) : <span className="text-gray-600 text-xs">—</span>}
                    </td>
                    <td className="px-4 py-3">
                      {w.score?.copyability != null ? (
                        <ScoreBar value={w.score.copyability} color="bg-blue-500" />
                      ) : <span className="text-gray-600 text-xs">—</span>}
                    </td>
                    <td className={`px-4 py-3 tabular-nums text-sm ${(w.score?.roi ?? 0) > 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                      {w.score?.roi != null ? fmtPct(w.score.roi) : '—'}
                    </td>
                    <td className="px-4 py-3 tabular-nums text-xs text-gray-300">
                      {w.score?.hit_rate != null ? fmtPct(w.score.hit_rate) : '—'}
                    </td>
                    <td className="px-4 py-3 tabular-nums text-xs text-gray-400">
                      {w.score?.max_drawdown != null ? `-${(w.score.max_drawdown * 100).toFixed(1)}%` : '—'}
                    </td>
                    <td className="px-4 py-3 text-xs tabular-nums">
                      {(w.copy_performance?.copied_trade_count ?? 0) === 0
                        ? <span className="text-gray-600">—</span>
                        : <span className={(w.copy_performance?.copied_realized_pnl ?? 0) >= 0 ? 'text-emerald-400' : 'text-red-400'}>
                            {(w.copy_performance?.copied_realized_pnl ?? 0) >= 0 ? '+' : ''}${(w.copy_performance?.copied_realized_pnl ?? 0).toFixed(2)}
                            <span className="text-gray-600 ml-1">({w.copy_performance?.copied_trade_count})</span>
                          </span>
                      }
                    </td>
                    <td className="px-4 py-3 text-xs">
                      {(() => {
                        const label = w.live_status?.activity_label ?? 'UNKNOWN';
                        const lastAt = w.live_status?.last_trade_at;
                        return (
                          <>
                            <span className={`rounded px-1.5 py-0.5 text-[9px] font-bold ${
                              label === 'ACTIVE'  ? 'bg-emerald-500/15 text-emerald-400' :
                              label === 'WARM'    ? 'bg-yellow-500/10 text-yellow-400' :
                              label === 'DORMANT' ? 'bg-red-500/10 text-red-400' :
                              'bg-white/5 text-gray-600'
                            }`}>{label}</span>
                            {lastAt && (
                              <div className="text-[9px] text-gray-600 mt-0.5">
                                {new Date(lastAt).toLocaleDateString('tr-TR', { timeZone: 'Europe/Istanbul', day: 'numeric', month: 'short' })}
                              </div>
                            )}
                          </>
                        );
                      })()}
                    </td>
                    <td className="px-4 py-3">
                      <span className={`rounded px-2 py-0.5 text-[10px] font-medium ${CLASSIFICATION_COLORS[w.score?.classification?.toLowerCase() ?? 'unknown'] ?? CLASSIFICATION_COLORS.unknown}`}>
                        {w.score?.classification || 'unscored'}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      {!w.address.startsWith('0xdemo') ? (
                        <span className="rounded bg-emerald-500/10 px-1.5 py-0.5 text-[10px] text-emerald-400">LIVE</span>
                      ) : (
                        <span className="rounded bg-white/5 px-1.5 py-0.5 text-[10px] text-gray-500">DEMO</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {wallets.length === 0 && (
              <div className="py-12 text-center text-sm text-gray-500">No wallets found.</div>
            )}
          </div>
        )}
      </div>
    </>
  );
}
