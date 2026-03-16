'use client';

import { useEffect, useState, useCallback, useRef } from 'react';
import { apiFetch } from '@/lib/api';
import { fmtDate } from '@/lib/format';

const POLL_MS = 10_000;
const PAGE_SIZE = 30;

/* ─── Types ─────────────────────────────────────────────── */
interface Snapshot {
  captured_at: string;
  best_bid: number | null;
  best_ask: number | null;
  midpoint: number | null;
  spread: number | null;
  last_trade_price: number | null;
  volume_24h: number | null;
  bid_depth: number | null;
  ask_depth: number | null;
}

interface MarketItem {
  id: string;
  question: string;
  slug: string;
  category: string;
  is_active: boolean;
  fees_enabled: boolean;
  fee_rate_bps: number | null;
  outcomes: string[];
  snapshot: Snapshot | null;
}

interface MarketResponse {
  total: number;
  markets: MarketItem[];
}

interface Relationship {
  id: string;
  market_a_question: string | null;
  market_b_question: string | null;
  type: string;
  asset: string | null;
  normal_spread_mean: number | null;
  normal_spread_std: number | null;
  price_a: number | null;
  price_b: number | null;
  current_spread: number | null;
  z_score: number | null;
  is_dislocation: boolean;
}

interface ActionItem {
  id: string;
  asset: string | null;
  type: string;
  market_a: string | null;
  market_b: string | null;
  price_a: number;
  price_b: number;
  fair_value: number;
  underpriced_price: number;
  current_spread: number;
  normal_spread_mean: number;
  z_score: number;
  is_dislocation: boolean;
  raw_edge: number;
  total_cost: number;
  net_edge: number;
  edge_to_cost_ratio: number;
  confidence_adjusted_edge: number;
  confidence_factor: number;
  staleness_haircut: number;
  slippage_haircut: number;
  depth_a: number;
  depth_b: number;
  min_depth: number;
  depth_ok: boolean;
  spread_a: number;
  spread_b: number;
  max_spread_pct: number;
  spread_ok: boolean;
  max_slippage_risk: number;
  slippage_ok: boolean;
  snap_age_seconds: number | null;
  data_fresh: boolean;
  holding_horizon: string;
  suggested_size_usd: number;
  why_not: string | null;
  dislocations_24h: number;
  verdict: string;
  verdict_color: string;
}

interface ActionabilityResponse {
  total: number;
  tradeable: number;
  marginal: number;
  precision_total: number;
  precision_profitable: number;
  items: ActionItem[];
}

interface PrecisionWindow {
  total_closed: number;
  profitable: number;
  unprofitable: number;
  precision: number | null;
  avg_pnl: number | null;
  total_pnl: number | null;
}

interface PrecisionResponse {
  grade: string;
  interpretation: string;
  windows: {
    '24h': PrecisionWindow;
    '7d': PrecisionWindow;
    'all_time': PrecisionWindow;
  };
}

/* ─── Verdict config ─────────────────────────────────────── */
const VERDICT_CONFIG: Record<string, { label: string; border: string; bg: string; badge: string; icon: string }> = {
  TRADEABLE:       { label: 'Tradeable',        border: 'border-emerald-500/40', bg: 'bg-emerald-500/5',  badge: 'bg-emerald-500/20 text-emerald-400', icon: '\u2714' },
  MARGINAL:        { label: 'Marginal',          border: 'border-yellow-500/30',  bg: 'bg-yellow-500/5',   badge: 'bg-yellow-500/20 text-yellow-400',   icon: '~' },
  SLIPPAGE_RISK:   { label: 'Slippage Risk',     border: 'border-orange-500/30',  bg: 'bg-orange-500/5',   badge: 'bg-orange-500/20 text-orange-400',   icon: '!' },
  LOW_LIQUIDITY:   { label: 'Low Liquidity',     border: 'border-orange-500/30',  bg: 'bg-orange-500/5',   badge: 'bg-orange-500/20 text-orange-400',   icon: '!' },
  SPREAD_TOO_WIDE: { label: 'Spread Too Wide',   border: 'border-red-500/20',     bg: 'bg-red-500/5',      badge: 'bg-red-500/20 text-red-400',         icon: '\u2717' },
  NO_EDGE:         { label: 'No Edge',           border: 'border-red-500/20',     bg: 'bg-red-500/5',      badge: 'bg-red-500/20 text-red-400',         icon: '\u2717' },
  STALE_DATA:      { label: 'Stale Data',        border: 'border-gray-500/20',    bg: 'bg-gray-500/5',     badge: 'bg-gray-500/20 text-gray-400',       icon: '?' },
  NO_SIGNAL:       { label: 'No Signal',         border: 'border-white/5',        bg: '',                  badge: 'bg-white/5 text-gray-500',           icon: '\u2013' },
};

const GRADE_CONFIG: Record<string, { color: string; label: string }> = {
  A: { color: 'text-emerald-400', label: 'Excellent' },
  B: { color: 'text-green-400',   label: 'Good' },
  C: { color: 'text-yellow-400',  label: 'Fair' },
  D: { color: 'text-red-400',     label: 'Poor' },
  'N/A': { color: 'text-gray-500', label: 'No data yet' },
};

const HORIZON_CONFIG: Record<string, { label: string; color: string }> = {
  short:    { label: 'Short (<5min)',    color: 'text-emerald-400' },
  medium:   { label: 'Medium (5-30min)', color: 'text-amber-400' },
  unstable: { label: 'Unstable',         color: 'text-orange-400' },
  none:     { label: '\u2014',            color: 'text-gray-600' },
};

/* ─── Helpers ────────────────────────────────────────────── */
const CATEGORY_COLORS: Record<string, string> = {
  crypto: 'bg-amber-500/15 text-amber-400',
  politics: 'bg-blue-500/15 text-blue-400',
  general: 'bg-purple-500/15 text-purple-400',
};

function fmt(v: number | null | undefined, decimals = 4): string {
  if (v == null) return '-';
  return v.toFixed(decimals);
}

function fmtVol(v: number | null | undefined): string {
  if (v == null) return '-';
  if (v >= 1_000_000) return `$${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1_000) return `$${(v / 1_000).toFixed(0)}K`;
  return `$${v.toFixed(0)}`;
}

function ProbBar({ value }: { value: number | null }) {
  if (value == null) return <span className="text-gray-600 text-xs">-</span>;
  const pct = Math.round(value * 100);
  const color = pct >= 70 ? 'bg-emerald-500' : pct >= 40 ? 'bg-amber-500' : 'bg-red-500';
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-20 shrink-0 rounded-full bg-white/10">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="tabular-nums text-xs text-gray-200">{pct}%</span>
    </div>
  );
}

function ZBadge({ z }: { z: number | null }) {
  if (z == null) return <span className="text-gray-600">-</span>;
  const abs = Math.abs(z);
  const color = abs >= 4 ? 'text-red-400 font-bold' : abs >= 2 ? 'text-amber-400 font-medium' : 'text-gray-400';
  return <span className={`tabular-nums text-xs ${color}`}>{z > 0 ? '+' : ''}{z.toFixed(2)}</span>;
}

function SpreadBar({ current, mean, std }: { current: number | null; mean: number | null; std: number | null }) {
  if (current == null) return <span className="text-gray-600 text-xs">-</span>;
  const maxDisplay = mean && std ? mean + std * 4 : 0.3;
  const pct = Math.min((current / maxDisplay) * 100, 100);
  const isHot = mean && std ? current > mean + std * 2 : false;
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-16 shrink-0 rounded-full bg-white/10">
        <div className={`h-full rounded-full ${isHot ? 'bg-red-500' : 'bg-cyan-600'}`} style={{ width: `${pct}%` }} />
      </div>
      <span className={`tabular-nums text-xs ${isHot ? 'text-red-400' : 'text-gray-400'}`}>{current.toFixed(4)}</span>
    </div>
  );
}

/* ─── Precision Panel ────────────────────────────────────── */
function PrecisionPanel({ data }: { data: PrecisionResponse }) {
  const gradeConf = GRADE_CONFIG[data.grade] ?? GRADE_CONFIG['N/A'];
  const windows = [
    { key: '24h', label: '24 hours', d: data.windows['24h'] },
    { key: '7d', label: '7 days', d: data.windows['7d'] },
    { key: 'all_time', label: 'All time', d: data.windows['all_time'] },
  ];
  return (
    <div className="rounded-xl border border-white/10 bg-surface-raised p-4">
      <div className="flex items-start justify-between mb-3">
        <div>
          <h3 className="text-sm font-semibold">Actionability Precision</h3>
          <p className="text-[11px] text-gray-500 mt-0.5">{data.interpretation}</p>
        </div>
        <div className="text-right">
          <p className="text-[10px] text-gray-500 uppercase tracking-wide">Grade</p>
          <p className={`text-3xl font-black tabular-nums ${gradeConf.color}`}>{data.grade}</p>
          <p className={`text-[10px] ${gradeConf.color}`}>{gradeConf.label}</p>
        </div>
      </div>
      <div className="grid grid-cols-3 gap-3">
        {windows.map(({ key, label, d }) => {
          const pct = d.precision != null ? (d.precision * 100).toFixed(0) : null;
          const barColor = d.precision == null ? 'bg-gray-600' : d.precision >= 0.6 ? 'bg-emerald-500' : d.precision >= 0.5 ? 'bg-green-500' : d.precision >= 0.4 ? 'bg-yellow-500' : 'bg-red-500';
          return (
            <div key={key} className="rounded-lg border border-white/5 bg-white/[0.02] p-3">
              <p className="text-[10px] uppercase tracking-wide text-gray-500">{label}</p>
              <p className="mt-1 text-lg font-bold tabular-nums">
                {pct != null ? <span className={barColor.replace('bg-', 'text-')}>{pct}%</span> : <span className="text-gray-600">N/A</span>}
              </p>
              <div className="mt-1 h-1 rounded-full bg-white/10">
                <div className={`h-full rounded-full ${barColor}`} style={{ width: `${pct ?? 0}%` }} />
              </div>
              <p className="mt-1.5 text-[10px] text-gray-600">
                {d.profitable}/{d.total_closed} profitable
                {d.avg_pnl != null && ` · avg $${d.avg_pnl.toFixed(2)}`}
              </p>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ─── Actionability Card ─────────────────────────────────── */
function ActionCard({ item }: { item: ActionItem }) {
  const cfg = VERDICT_CONFIG[item.verdict] ?? VERDICT_CONFIG.NO_SIGNAL;
  const [expanded, setExpanded] = useState(false);
  const horizon = HORIZON_CONFIG[item.holding_horizon] ?? HORIZON_CONFIG.none;

  const checks: { label: string; ok: boolean; value: string }[] = [
    { label: 'Signal strength', ok: item.is_dislocation, value: `z = ${item.z_score > 0 ? '+' : ''}${item.z_score.toFixed(2)}` },
    { label: 'Positive net edge', ok: item.net_edge > 0, value: `${item.net_edge > 0 ? '+' : ''}${item.net_edge.toFixed(4)}` },
    { label: 'Edge > cost (ratio)', ok: item.edge_to_cost_ratio > 1.5, value: `${item.edge_to_cost_ratio.toFixed(2)}x` },
    { label: 'Min depth >= $20', ok: item.depth_ok, value: `$${item.min_depth.toFixed(0)}` },
    { label: 'Spread manageable', ok: item.spread_ok, value: `${(item.max_spread_pct * 100).toFixed(1)}%` },
    { label: 'Slippage < 30%', ok: item.slippage_ok, value: `${(item.max_slippage_risk * 100).toFixed(1)}%` },
    { label: 'Data fresh', ok: item.data_fresh, value: item.snap_age_seconds != null ? `${item.snap_age_seconds}s ago` : 'unknown' },
  ];

  const conservativeEdge = item.confidence_adjusted_edge;
  const suggestSize = item.suggested_size_usd;

  return (
    <div className={`rounded-xl border p-4 transition-all ${cfg.border} ${cfg.bg}`}>
      {/* Header row */}
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2 mb-2">
            <span className={`rounded px-2.5 py-0.5 text-[11px] font-bold tracking-wide ${cfg.badge}`}>
              {cfg.icon} {cfg.label.toUpperCase()}
            </span>
            {item.asset && (
              <span className={`rounded px-2 py-0.5 text-[10px] font-medium ${CATEGORY_COLORS.crypto}`}>
                {item.asset}
              </span>
            )}
            <span className="rounded bg-white/5 px-2 py-0.5 text-[10px] text-gray-400">
              {item.type?.replace(/_/g, ' ')}
            </span>
            {item.dislocations_24h > 0 && (
              <span className="rounded bg-blue-500/10 px-2 py-0.5 text-[10px] text-blue-400">
                {item.dislocations_24h}x today
              </span>
            )}
            {item.snap_age_seconds != null && (
              <span className={`rounded px-2 py-0.5 text-[9px] ${item.snap_age_seconds < 60 ? 'bg-emerald-500/10 text-emerald-400' : item.snap_age_seconds < 180 ? 'bg-yellow-500/10 text-yellow-400' : 'bg-red-500/10 text-red-400'}`}>
                {item.snap_age_seconds < 60 ? 'LIVE' : item.snap_age_seconds < 180 ? `${item.snap_age_seconds}s` : `STALE ${item.snap_age_seconds}s`}
              </span>
            )}
          </div>
          {/* Why not — primary blocker */}
          {item.why_not && (
            <p className="mb-1.5 text-[10px] text-red-400/80">&#9888; {item.why_not}</p>
          )}
          <div className="flex items-center gap-2 text-xs text-gray-400">
            <span className="truncate max-w-[240px] text-gray-300">{item.market_a}</span>
            <span className="shrink-0 text-gray-600">vs</span>
            <span className="truncate max-w-[240px] text-gray-300">{item.market_b}</span>
          </div>
        </div>
        <button
          onClick={() => setExpanded(!expanded)}
          className="shrink-0 rounded border border-white/10 px-2.5 py-1 text-[11px] text-gray-500 hover:text-white"
        >
          {expanded ? 'less' : 'details'}
        </button>
      </div>

      {/* Key metrics row */}
      <div className="mt-3 grid grid-cols-4 gap-3 sm:grid-cols-8 text-xs">
        <div className="col-span-2">
          <p className="text-[10px] text-gray-500">Z-Score</p>
          <ZBadge z={item.z_score} />
        </div>
        <div className="col-span-2">
          <p className="text-[10px] text-gray-500">Net Edge</p>
          <span className={`tabular-nums font-semibold ${item.net_edge > 0 ? 'text-emerald-400' : 'text-red-400'}`}>
            {item.net_edge > 0 ? '+' : ''}{item.net_edge.toFixed(4)}
          </span>
        </div>
        <div className="col-span-2">
          <p className="text-[10px] text-gray-500">Conservative Edge</p>
          <span className={`tabular-nums font-semibold ${conservativeEdge > 0 ? 'text-cyan-400' : 'text-red-400'}`}>
            {conservativeEdge > 0 ? '+' : ''}{conservativeEdge.toFixed(4)}
            <span className="ml-1 text-gray-600 text-[9px]">({(item.confidence_factor * 100).toFixed(0)}%)</span>
          </span>
        </div>
        <div className="col-span-2">
          <p className="text-[10px] text-gray-500">Min Depth</p>
          <span className={`tabular-nums ${item.depth_ok ? 'text-gray-300' : 'text-red-400'}`}>
            ${item.min_depth.toFixed(0)}
          </span>
        </div>
      </div>

      {/* Execution guidance row */}
      <div className="mt-2 flex flex-wrap items-center gap-4 text-xs border-t border-white/5 pt-2">
        <div>
          <span className="text-gray-600">Horizon: </span>
          <span className={`font-medium ${horizon.color}`}>{horizon.label}</span>
        </div>
        <div>
          <span className="text-gray-600">Suggested size: </span>
          <span className={`font-semibold tabular-nums ${suggestSize > 0 ? 'text-emerald-400' : 'text-gray-500'}`}>
            {suggestSize > 0 ? `$${suggestSize.toFixed(0)}` : '$0'}
          </span>
        </div>
        {item.dislocations_24h > 0 && (
          <div>
            <span className="text-gray-600">24h dislocations: </span>
            <span className="font-medium text-blue-400">{item.dislocations_24h}</span>
          </div>
        )}
      </div>

      {/* Expanded detail */}
      {expanded && (
        <div className="mt-4 space-y-3 border-t border-white/5 pt-4">
          {/* Checklist */}
          <div className="grid grid-cols-2 gap-x-6 gap-y-1.5 text-xs">
            {checks.map(({ label, ok, value }) => (
              <div key={label} className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-1.5">
                  <span className={ok ? 'text-emerald-400' : 'text-red-400'}>{ok ? '\u2714' : '\u2717'}</span>
                  <span className="text-gray-400">{label}</span>
                </div>
                <span className={`tabular-nums font-medium ${ok ? 'text-gray-200' : 'text-red-400'}`}>{value}</span>
              </div>
            ))}
          </div>

          {/* Price & cost breakdown */}
          <div className="rounded-lg bg-white/[0.03] p-3 text-xs space-y-1">
            <p className="text-[10px] font-semibold uppercase tracking-wide text-gray-500">Price & Cost Breakdown</p>
            <div className="grid grid-cols-3 gap-x-4 gap-y-1 mt-1 text-gray-400">
              <span>Price A: <span className="text-gray-200">{item.price_a.toFixed(4)}</span></span>
              <span>Price B: <span className="text-gray-200">{item.price_b.toFixed(4)}</span></span>
              <span>Fair value: <span className="text-gray-200">{item.fair_value.toFixed(4)}</span></span>
              <span>Raw edge: <span className="text-emerald-400">+{item.raw_edge.toFixed(4)}</span></span>
              <span>Total cost: <span className="text-amber-400">-{item.total_cost.toFixed(4)}</span></span>
              <span>Net edge: <span className={item.net_edge > 0 ? 'text-emerald-400' : 'text-red-400'}>{item.net_edge > 0 ? '+' : ''}{item.net_edge.toFixed(4)}</span></span>
              <span>Staleness HC: <span className="text-gray-300">{(item.staleness_haircut * 100).toFixed(0)}%</span></span>
              <span>Slippage HC: <span className="text-gray-300">{(item.slippage_haircut * 100).toFixed(0)}%</span></span>
              <span>Cons. edge: <span className={conservativeEdge > 0 ? 'text-cyan-400' : 'text-red-400'}>{conservativeEdge > 0 ? '+' : ''}{conservativeEdge.toFixed(4)}</span></span>
              <span>Spread A: <span className="text-gray-300">{item.spread_a.toFixed(4)}</span></span>
              <span>Spread B: <span className="text-gray-300">{item.spread_b.toFixed(4)}</span></span>
              <span>Max sprd%: <span className={item.spread_ok ? 'text-gray-300' : 'text-red-400'}>{(item.max_spread_pct * 100).toFixed(1)}%</span></span>
              <span>Depth A: <span className="text-gray-300">${item.depth_a.toFixed(0)}</span></span>
              <span>Depth B: <span className="text-gray-300">${item.depth_b.toFixed(0)}</span></span>
              <span>Slip risk: <span className={item.slippage_ok ? 'text-gray-300' : 'text-orange-400'}>{(item.max_slippage_risk * 100).toFixed(1)}%</span></span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/* ─── Main Page ──────────────────────────────────────────── */
export default function MarketsPage() {
  const [tab, setTab] = useState<'actionability' | 'markets' | 'relationships'>('actionability');
  const [markets, setMarkets] = useState<MarketItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [filterCat, setFilterCat] = useState('');
  const [rels, setRels] = useState<Relationship[]>([]);
  const [actionData, setActionData] = useState<ActionabilityResponse | null>(null);
  const [precision, setPrecision] = useState<PrecisionResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);
  const prevSnaps = useRef<Record<string, number>>({});

  const fetchMarkets = useCallback(async (resetPage = false) => {
    setLoading(true);
    const p = resetPage ? 0 : page;
    const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(p * PAGE_SIZE), active_only: 'true' });
    if (filterCat) params.set('category', filterCat);
    try {
      const data = await apiFetch<MarketResponse>(`/api/markets?${params}`);
      setMarkets(data.markets);
      setTotal(data.total);
      setLastRefresh(new Date());
      if (resetPage) setPage(0);
    } catch { /* ignore */ }
    finally { setLoading(false); }
  }, [page, filterCat]);

  const fetchRels = useCallback(async () => {
    try {
      const data = await apiFetch<{ relationships: Relationship[] }>('/api/markets/relationships');
      setRels(data.relationships);
    } catch { /* ignore */ }
  }, []);

  const fetchActionability = useCallback(async () => {
    try {
      const [actData, precData] = await Promise.all([
        apiFetch<ActionabilityResponse>('/api/markets/actionability'),
        apiFetch<PrecisionResponse>('/api/markets/actionability-precision'),
      ]);
      setActionData(actData);
      setPrecision(precData);
      setLastRefresh(new Date());
    } catch { /* ignore */ }
  }, []);

  useEffect(() => { fetchMarkets(true); fetchRels(); fetchActionability(); }, []);
  useEffect(() => { fetchMarkets(true); }, [filterCat]);
  useEffect(() => { fetchMarkets(false); }, [page]);

  useEffect(() => {
    const fns: Record<string, () => void> = {
      actionability: fetchActionability,
      relationships: fetchRels,
      markets: () => { if (page === 0) fetchMarkets(false); },
    };
    const id = setInterval(fns[tab] ?? fetchActionability, POLL_MS);
    return () => clearInterval(id);
  }, [tab, page, fetchMarkets, fetchRels, fetchActionability]);

  const [flashIds, setFlashIds] = useState<Set<string>>(new Set());
  useEffect(() => {
    const changed = new Set<string>();
    markets.forEach((m) => {
      const mid = m.snapshot?.midpoint;
      if (mid != null && prevSnaps.current[m.id] != null && prevSnaps.current[m.id] !== mid) changed.add(m.id);
      if (mid != null) prevSnaps.current[m.id] = mid;
    });
    if (changed.size > 0) { setFlashIds(changed); setTimeout(() => setFlashIds(new Set()), 1500); }
  }, [markets]);

  const totalPages = Math.ceil(total / PAGE_SIZE);
  const categories = ['crypto', 'general', 'politics'];
  const dislocationCount = rels.filter((r) => r.is_dislocation).length;
  const tradeableCount = actionData?.tradeable ?? 0;

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-xl font-bold">Market Monitor</h1>
          <p className="text-sm text-gray-500">Live prices, dislocations and actionability scoring</p>
        </div>
        <div className="flex items-center gap-3">
          {lastRefresh && (
            <span className="text-xs text-gray-600">
              {loading
                ? <span className="inline-flex items-center gap-1"><span className="h-1.5 w-1.5 animate-pulse rounded-full bg-blue-400" />updating...</span>
                : <>updated {lastRefresh.toLocaleTimeString('tr-TR', { timeZone: 'Europe/Istanbul' })}</>
              }
            </span>
          )}
          <button onClick={() => { fetchMarkets(false); fetchRels(); fetchActionability(); }} disabled={loading}
            className="rounded-lg border border-white/10 bg-white/5 px-3 py-1.5 text-xs hover:bg-white/10 disabled:opacity-40">
            Refresh
          </button>
        </div>
      </div>

      {/* Stats bar */}
      <div className="grid grid-cols-5 gap-3">
        {[
          { label: 'Active Markets', value: total },
          { label: 'Tracked Pairs', value: rels.length },
          { label: 'Dislocations Now', value: dislocationCount, hot: dislocationCount > 0 },
          { label: 'Tradeable Now', value: tradeableCount, hot: tradeableCount > 0, highlight: true },
          {
            label: 'Data Freshness',
            value: actionData?.items?.[0]?.snap_age_seconds != null
              ? `${actionData.items[0].snap_age_seconds}s`
              : '—',
            sub: 'latest snap age',
            hot: (actionData?.items?.[0]?.snap_age_seconds ?? 999) < 120,
          },
        ].map(({ label, value, hot, highlight, sub }) => (
          <div key={label} className={`rounded-xl border p-3 transition-colors ${highlight && hot ? 'border-emerald-500/40 bg-emerald-500/5' : hot ? 'border-emerald-500/20 bg-emerald-500/5' : 'border-white/5 bg-surface-raised'}`}>
            <p className="text-[10px] uppercase tracking-wide text-gray-500">{label}</p>
            {sub && <p className="text-[9px] text-gray-600">{sub}</p>}
            <p className={`mt-1 text-xl font-bold tabular-nums ${highlight && hot ? 'text-emerald-400' : hot ? 'text-emerald-400' : ''}`}>{value}</p>
          </div>
        ))}
      </div>

      {/* Tab switcher */}
      <div className="flex gap-1 rounded-xl border border-white/5 bg-surface-raised p-1 w-fit">
        {([
          ['actionability', `Actionability (${actionData?.total ?? 0})`],
          ['relationships', `Pairs & Z-Score (${rels.length})`],
          ['markets', `All Markets (${total})`],
        ] as const).map(([t, label]) => (
          <button key={t} onClick={() => setTab(t)}
            className={`rounded-lg px-4 py-2 text-sm font-medium transition-colors ${tab === t ? 'bg-white/10 text-white' : 'text-gray-500 hover:text-gray-300'}`}>
            {label}
          </button>
        ))}
      </div>

      {/* ── ACTIONABILITY TAB ── */}
      {tab === 'actionability' && (
        <div className="space-y-4">
          {/* Precision Panel */}
          {precision && <PrecisionPanel data={precision} />}

          {/* Legend */}
          <div className="flex flex-wrap items-center gap-2 text-[11px]">
            <span className="text-gray-500">Verdict key:</span>
            {Object.entries(VERDICT_CONFIG).map(([key, cfg]) => (
              <span key={key} className={`rounded px-2 py-0.5 font-medium ${cfg.badge}`}>{cfg.icon} {cfg.label}</span>
            ))}
          </div>

          {!actionData && <p className="text-sm text-gray-500">Loading actionability data...</p>}

          {actionData?.items.length === 0 && (
            <p className="text-sm text-gray-500">No active relationships configured.</p>
          )}

          {actionData && actionData.items.filter(i => i.verdict !== 'NO_SIGNAL').length > 0 && (
            <div className="rounded-lg border border-white/5 bg-surface-raised px-4 py-2 text-xs text-gray-400">
              {tradeableCount > 0 && <span className="text-emerald-400 font-semibold mr-3">{tradeableCount} tradeable</span>}
              {(actionData.marginal) > 0 && <span className="text-yellow-400 mr-3">~ {actionData.marginal} marginal</span>}
              <span className="text-gray-500">
                {actionData.items.filter(i => ['NO_EDGE','LOW_LIQUIDITY','SPREAD_TOO_WIDE','SLIPPAGE_RISK','STALE_DATA'].includes(i.verdict)).length} blocked
              </span>
            </div>
          )}

          {actionData?.items.map((item) => (
            <ActionCard key={item.id} item={item} />
          ))}
        </div>
      )}

      {/* ── RELATIONSHIPS TAB ── */}
      {tab === 'relationships' && (
        <div className="space-y-3">
          {rels.length === 0 ? (
            <p className="text-sm text-gray-500">No active market relationships configured.</p>
          ) : (
            rels.map((r) => (
              <div key={r.id} className={`rounded-xl border p-4 transition-colors ${r.is_dislocation ? 'border-amber-500/40 bg-amber-500/5' : 'border-white/5 bg-surface-raised'}`}>
                <div className="mb-3 flex items-start justify-between gap-4">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      {r.is_dislocation && (
                        <span className="rounded bg-amber-500/20 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-amber-400">Dislocation</span>
                      )}
                      <span className={`rounded px-2 py-0.5 text-[10px] font-medium ${CATEGORY_COLORS['crypto'] ?? 'bg-white/5 text-gray-400'}`}>{r.asset ?? 'multi'}</span>
                      <span className="rounded bg-white/5 px-2 py-0.5 text-[10px] text-gray-400">{r.type?.replace(/_/g, ' ')}</span>
                    </div>
                    <div className="mt-2 flex items-center gap-3 text-xs">
                      <span className="text-gray-300 truncate max-w-[280px]">{r.market_a_question}</span>
                      <span className="shrink-0 text-gray-600">vs</span>
                      <span className="text-gray-300 truncate max-w-[280px]">{r.market_b_question}</span>
                    </div>
                  </div>
                  <div className="text-right shrink-0">
                    <p className="text-[10px] text-gray-500">Z-Score</p>
                    <ZBadge z={r.z_score} />
                  </div>
                </div>
                <div className="grid grid-cols-5 gap-3 text-xs">
                  <div><p className="text-[10px] text-gray-500">Price A</p><p className="mt-0.5 tabular-nums text-gray-200">{fmt(r.price_a)}</p></div>
                  <div><p className="text-[10px] text-gray-500">Price B</p><p className="mt-0.5 tabular-nums text-gray-200">{fmt(r.price_b)}</p></div>
                  <div><p className="text-[10px] text-gray-500">Current Spread</p><SpreadBar current={r.current_spread} mean={r.normal_spread_mean} std={r.normal_spread_std} /></div>
                  <div><p className="text-[10px] text-gray-500">Normal Mean</p><p className="mt-0.5 tabular-nums text-gray-400">{fmt(r.normal_spread_mean)}</p></div>
                  <div><p className="text-[10px] text-gray-500">Normal Std</p><p className="mt-0.5 tabular-nums text-gray-400">{fmt(r.normal_spread_std)}</p></div>
                </div>
              </div>
            ))
          )}
        </div>
      )}

      {/* ── MARKETS TAB ── */}
      {tab === 'markets' && (
        <>
          <div className="flex items-center gap-1.5 rounded-lg border border-white/10 bg-surface-raised px-1 py-1 w-fit">
            <button onClick={() => setFilterCat('')} className={`rounded px-3 py-1 text-xs transition-colors ${!filterCat ? 'bg-white/10 text-white' : 'text-gray-500 hover:text-gray-300'}`}>All</button>
            {categories.map((c) => (
              <button key={c} onClick={() => setFilterCat(filterCat === c ? '' : c)}
                className={`rounded px-3 py-1 text-xs transition-colors ${filterCat === c ? (CATEGORY_COLORS[c] ?? 'bg-white/10') : 'text-gray-500 hover:text-gray-300'}`}>
                {c}
              </button>
            ))}
          </div>

          <div className="overflow-x-auto rounded-xl border border-white/5">
            <table className="w-full text-sm">
              <thead className="border-b border-white/5 bg-surface-raised text-left text-xs text-gray-500">
                <tr>
                  <th className="px-4 py-3">Market</th>
                  <th className="px-4 py-3">Category</th>
                  <th className="px-4 py-3">Midpoint</th>
                  <th className="px-4 py-3">Probability</th>
                  <th className="px-4 py-3">Bid / Ask</th>
                  <th className="px-4 py-3">Spread</th>
                  <th className="px-4 py-3">Volume 24h</th>
                  <th className="px-4 py-3">Last Trade</th>
                  <th className="px-4 py-3">Updated</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5">
                {markets.map((m) => {
                  const s = m.snapshot;
                  const isFlashing = flashIds.has(m.id);
                  return (
                    <tr key={m.id} className={`transition-colors duration-700 hover:bg-white/[0.025] ${isFlashing ? 'bg-amber-500/10' : ''}`}>
                      <td className="max-w-xs px-4 py-3">
                        <p className="truncate text-xs text-gray-200 leading-tight">{m.question}</p>
                        {m.fees_enabled && <span className="mt-0.5 inline-block rounded bg-amber-500/10 px-1.5 py-0 text-[9px] text-amber-400">fee {m.fee_rate_bps}bps</span>}
                      </td>
                      <td className="px-4 py-3">
                        <span className={`rounded px-2 py-0.5 text-[10px] font-medium ${CATEGORY_COLORS[m.category] ?? 'bg-white/5 text-gray-400'}`}>{m.category}</span>
                      </td>
                      <td className="px-4 py-3 tabular-nums text-sm font-medium">
                        {s?.midpoint != null ? <span className={isFlashing ? 'text-amber-400' : 'text-white'}>{fmt(s.midpoint)}</span> : <span className="text-gray-600">-</span>}
                      </td>
                      <td className="px-4 py-3"><ProbBar value={s?.midpoint ?? null} /></td>
                      <td className="px-4 py-3 tabular-nums text-xs text-gray-400">
                        <span className="text-emerald-400">{fmt(s?.best_bid)}</span>{' / '}<span className="text-red-400">{fmt(s?.best_ask)}</span>
                      </td>
                      <td className="px-4 py-3">
                        {s?.spread != null ? <span className={`tabular-nums text-xs ${s.spread > 0.05 ? 'text-red-400' : 'text-gray-400'}`}>{fmt(s.spread)}</span> : <span className="text-gray-600 text-xs">-</span>}
                      </td>
                      <td className="px-4 py-3 tabular-nums text-xs text-gray-400">{fmtVol(s?.volume_24h)}</td>
                      <td className="px-4 py-3 tabular-nums text-xs text-gray-400">{fmt(s?.last_trade_price)}</td>
                      <td className="px-4 py-3 text-[11px] text-gray-600">{s?.captured_at ? fmtDate(s.captured_at) : '-'}</td>
                    </tr>
                  );
                })}
                {markets.length === 0 && !loading && (
                  <tr><td colSpan={9} className="px-4 py-12 text-center text-gray-500">No markets found.</td></tr>
                )}
              </tbody>
            </table>
          </div>

          {totalPages > 1 && (
            <div className="flex items-center justify-between">
              <span className="text-xs text-gray-500">{page * PAGE_SIZE + 1}–{Math.min((page + 1) * PAGE_SIZE, total)} / {total}</span>
              <div className="flex items-center gap-1">
                <button onClick={() => setPage(0)} disabled={page === 0} className="rounded border border-white/10 px-2.5 py-1.5 text-xs text-gray-400 hover:text-white disabled:opacity-30">«</button>
                <button onClick={() => setPage((p) => Math.max(0, p - 1))} disabled={page === 0} className="rounded border border-white/10 px-2.5 py-1.5 text-xs text-gray-400 hover:text-white disabled:opacity-30">‹</button>
                {Array.from({ length: Math.min(7, totalPages) }, (_, i) => {
                  const start = Math.max(0, Math.min(page - 3, totalPages - 7));
                  const p = start + i;
                  return <button key={p} onClick={() => setPage(p)} className={`min-w-[32px] rounded border px-2.5 py-1.5 text-xs transition-colors ${p === page ? 'border-blue-500/50 bg-blue-500/10 text-blue-400' : 'border-white/10 text-gray-500 hover:text-white'}`}>{p + 1}</button>;
                })}
                <button onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))} disabled={page >= totalPages - 1} className="rounded border border-white/10 px-2.5 py-1.5 text-xs text-gray-400 hover:text-white disabled:opacity-30">›</button>
                <button onClick={() => setPage(totalPages - 1)} disabled={page >= totalPages - 1} className="rounded border border-white/10 px-2.5 py-1.5 text-xs text-gray-400 hover:text-white disabled:opacity-30">»</button>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
