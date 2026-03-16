'use client';

import { useEffect, useState } from 'react';
import { apiFetch } from '@/lib/api';

interface WalletResearch {
  wallet_id: string;
  label: string;
  address: string;
  information_score: number;
  edge_window_seconds: number;
  persistence_score: number;
  timing_verdict: string;
  persistence_verdict: string;
  combined_signal: number;
}

interface HeatmapCategory {
  category: string;
  market_count: number;
  avg_inefficiency: number;
  avg_spread: number;
  avg_volume_24h: number;
  verdict: string;
}

interface TimingProfile {
  wallet_id: string;
  label: string;
  address: string;
  information_score: number;
  edge_window_seconds: number;
  verdict: string;
  trade_count: number;
  price_move_by_horizon: Record<string, { avg_move: number; pct_positive: number; sample_count: number }>;
}

interface PersistenceProfile {
  wallet_id: string;
  label: string;
  is_real: boolean;
  persistence_score: number;
  autocorrelation: number;
  verdict: string;
  total_windows: number;
  windows: Array<{ window_start: string; roi_proxy: number; hit_rate: number; trade_count: number }>;
}

interface ClusterData {
  clusters: Array<{
    cluster_id: string;
    members: Array<{ wallet_id: string; label: string; address: string; is_real: boolean }>;
    avg_similarity: number;
    warning: string;
    evidence: string[];
  }>;
  total_wallets: number;
  clustered_wallets: number;
  insight: string;
}

interface ExperimentResult {
  params: { min_conf_edge: number; min_depth: number; min_z: number; max_spread_pct: number };
  current_snapshot: {
    total_pairs: number;
    passes_z: number;
    passes_depth: number;
    passes_spread: number;
    passes_edge: number;
    would_trade_now: number;
  };
  historical_precision: { total_closed: number; profitable: number; precision: number | null };
  gate_breakdown: Array<{
    pair: string;
    z: number;
    min_depth: number;
    spread_pct: number;
    conf_adj_edge: number;
    ok_z: boolean;
    ok_depth: boolean;
    ok_spread: boolean;
    ok_edge: boolean;
    would_trade: boolean;
  }>;
}

type ResearchTab = 'summary' | 'timing' | 'persistence' | 'heatmap' | 'clusters' | 'experiment' | 'matrix' | 'wallet_intel' | 'leader_impact';

interface MatrixCell {
  conf_edge: number;
  spread_ratio: number;
  depth: number;
  would_trade_now: number;
  pass_rate: number;
  est_precision: number;
  avg_conf_edge: number;
  ev_score: number;
  is_sweet_spot: boolean;
}

interface MatrixResult {
  total_pairs_monitored: number;
  hist_total_trades: number;
  hist_baseline_precision: number;
  sweet_spot: {
    conf_edge: number;
    spread_ratio: number;
    depth: number;
    would_trade_now: number;
    est_precision: number;
    ev_score: number;
  } | null;
  matrix: MatrixCell[];
  axes: {
    conf_edge_values: number[];
    spread_ratio_values: number[];
    depth_values: number[];
  };
}

// ── Wallet Intelligence types ──────────────────────────────────────────
interface AlphaLeaderEntry {
  wallet_id: string;
  address: string;
  label: string | null;
  copyable_alpha_score: number;
  recommendation: 'copy_now' | 'monitor' | 'avoid';
  alpha_decay_risk: 'low' | 'medium' | 'high' | 'critical';
  decay_signal: string;
  top_factors: {
    timing: number;
    persistence: number;
    latency_survivability: number;
    information_impact: number;
  };
}

interface AlphaLeaderboard {
  leaderboard: AlphaLeaderEntry[];
  total_evaluated: number;
  copy_now_count: number;
  monitor_count: number;
  avoid_count: number;
}

interface InfluenceEdge {
  leader: string;
  follower: string;
  market_id: string;
  mean_lag_seconds: number;
  trade_count: number;
  outcome_correlation: number;
  weight: number;
}

interface InfluenceGraph {
  node_count: number;
  edge_count: number;
  leader_count: number;
  follower_count: number;
  leaders: string[];
  followers: string[];
  top_edges: InfluenceEdge[];
  influence_scores: Record<string, number>;
  computed_at: string;
}

interface AlphaBacktestBucket {
  bucket: number;
  label: string;
  wallet_count: number;
  trade_count: number;
  avg_alpha_score: number;
  score_range: [number, number];
  total_pnl: number;
  profit_factor: number | null;
  win_rate: number | null;
  expectancy: number | null;
  verdict: string;
}

interface AlphaBacktest {
  buckets: AlphaBacktestBucket[];
  validation: {
    score_predictive: boolean;
    roughly_monotonic: boolean;
    top_bucket_pf: number;
    bottom_bucket_pf: number;
    verdict: string;
  };
  total_wallets_evaluated: number;
  total_trades_mapped: number;
}

interface CalibrationPoint {
  wallet_id: string;
  label: string;
  composite_score: number;
  copyable_alpha: number;
  realized_pnl: number;
  realized_pf: number | null;
  win_rate: number | null;
  trade_count: number;
  classification: string | null;
  has_data: boolean;
}

interface WalletCalibration {
  points: CalibrationPoint[];
  total_wallets: number;
  wallets_with_copy_data: number;
  calibration_score: number | null;
  calibration_verdict: string;
  avg_realized_pf: number | null;
  note: string;
}

function SignalBar({ value, label }: { value: number; label?: string }) {
  const pct = Math.max(0, Math.min(1, value)) * 100;
  const color = pct > 60 ? 'bg-green-500' : pct > 35 ? 'bg-yellow-500' : 'bg-red-500';
  return (
    <div className="flex items-center gap-2">
      <div className="h-2 w-24 rounded-full bg-white/10">
        <div className={`h-full rounded-full ${color} transition-all`} style={{ width: `${pct}%` }} />
      </div>
      <span className="tabular-nums text-xs text-gray-300">{pct.toFixed(0)}%</span>
      {label && <span className="text-xs text-gray-500">{label}</span>}
    </div>
  );
}

function VerdictChip({ verdict }: { verdict: string }) {
  const key = verdict.split(':')[0];
  const colors: Record<string, string> = {
    INFORMATION_EDGE: 'bg-green-500/20 text-green-400',
    POSSIBLE_EDGE: 'bg-emerald-500/20 text-emerald-400',
    STRONG_PERSISTENT: 'bg-green-500/20 text-green-400',
    STRONG_PERSISTENT_AND_IMPROVING: 'bg-green-600/20 text-green-300',
    MODERATE_PERSISTENT: 'bg-yellow-500/20 text-yellow-400',
    LAGGING: 'bg-red-500/20 text-red-400',
    NO_EDGE: 'bg-red-500/20 text-red-400',
    NOISE: 'bg-red-500/20 text-red-400',
    MEAN_REVERTING: 'bg-orange-500/20 text-orange-400',
    BORDERLINE: 'bg-gray-500/20 text-gray-400',
    INSUFFICIENT_DATA: 'bg-gray-600/20 text-gray-500',
  };
  return (
    <span className={`rounded px-2 py-0.5 text-[10px] font-medium ${colors[key] || 'bg-white/5 text-gray-400'}`}>
      {key.replace(/_/g, ' ')}
    </span>
  );
}

export default function ResearchPage() {
  const [tab, setTab] = useState<ResearchTab>('summary');
  const [summary, setSummary] = useState<{ top_wallets: WalletResearch[]; heatmap_summary: HeatmapCategory[] } | null>(null);
  const [timing, setTiming] = useState<{ profiles: TimingProfile[] } | null>(null);
  const [persistence, setPersistence] = useState<{ profiles: PersistenceProfile[] } | null>(null);
  const [heatmap, setHeatmap] = useState<{ categories: HeatmapCategory[]; top_markets: any[] } | null>(null);
  const [clusters, setClusters] = useState<ClusterData | null>(null);
  const [loading, setLoading] = useState(false);

  // Experiment Dashboard state
  const [expResult, setExpResult] = useState<ExperimentResult | null>(null);
  const [expLoading, setExpLoading] = useState(false);
  const [expParams, setExpParams] = useState({
    min_conf_edge: 0.005,
    min_depth: 20,
    min_z: 2.0,
    max_spread_pct: 0.12,
  });

  // Matrix Lab state
  const [matrixResult, setMatrixResult] = useState<MatrixResult | null>(null);
  const [matrixLoading, setMatrixLoading] = useState(false);
  const [matrixDepthFilter, setMatrixDepthFilter] = useState<number>(20);

  // Wallet Intelligence state
  const [alphaLeaderboard, setAlphaLeaderboard] = useState<AlphaLeaderboard | null>(null);
  const [influenceGraph, setInfluenceGraph] = useState<InfluenceGraph | null>(null);
  const [walletIntelLoading, setWalletIntelLoading] = useState(false);
  const [alphaBacktest, setAlphaBacktest] = useState<AlphaBacktest | null>(null);
  const [calibration, setCalibration] = useState<WalletCalibration | null>(null);

  // Leader Impact state
  const [leaderImpact, setLeaderImpact] = useState<{
    leaders: Array<{
      wallet_id: string;
      n_trades: number;
      n_with_price_data: number;
      avg_aligned_move_60s: number | null;
      hit_rate_60s: number | null;
      wallet_score: number | null;
      prop_signal: number;
      classification: string;
    }>;
    strong_leaders: number;
    total: number;
    note: string;
  } | null>(null);
  const [leaderImpactLoading, setLeaderImpactLoading] = useState(false);

  async function loadMatrix() {
    setMatrixLoading(true);
    try {
      const d = await apiFetch<MatrixResult>('/api/markets/param-matrix');
      setMatrixResult(d);
    } catch (e) {
      console.error(e);
    } finally {
      setMatrixLoading(false);
    }
  }

  async function loadTab(t: ResearchTab) {
    setTab(t);
    setLoading(true);
    try {
      if (t === 'summary' && !summary) {
        const d = await apiFetch<any>('/api/analytics/research-summary');
        setSummary(d);
      } else if (t === 'timing' && !timing) {
        const d = await apiFetch<any>('/api/analytics/timing');
        setTiming(d);
      } else if (t === 'persistence' && !persistence) {
        const d = await apiFetch<any>('/api/analytics/alpha-persistence');
        setPersistence(d);
      } else if (t === 'heatmap' && !heatmap) {
        const d = await apiFetch<any>('/api/analytics/market-heatmap');
        setHeatmap(d);
      } else if (t === 'clusters' && !clusters) {
        const d = await apiFetch<any>('/api/analytics/wallet-clusters');
        setClusters(d);
      } else if (t === 'experiment') {
        // Experiment tab has its own run button — just ensure tab is set
      } else if (t === 'matrix') {
        await loadMatrix();
      } else if (t === 'wallet_intel') {
        setWalletIntelLoading(true);
        try {
          const [lb, graph] = await Promise.all([
            apiFetch<AlphaLeaderboard>('/api/wallets/intelligence/alpha-leaderboard'),
            apiFetch<InfluenceGraph>('/api/wallets/intelligence/influence-graph'),
          ]);
          setAlphaLeaderboard(lb);
          setInfluenceGraph(graph);
          // Load backtest in background
          apiFetch<AlphaBacktest>('/api/analytics/alpha-backtest').then(setAlphaBacktest).catch(() => {});
          apiFetch<WalletCalibration>('/api/analytics/wallet-calibration').then(setCalibration).catch(() => {});
        } finally {
          setWalletIntelLoading(false);
        }
      } else if (t === 'leader_impact') {
        setLeaderImpactLoading(true);
        try {
          const d = await apiFetch<typeof leaderImpact>('/api/wallets/intelligence/leader-impact?min_score=0.4');
          setLeaderImpact(d);
        } finally {
          setLeaderImpactLoading(false);
        }
      }
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { loadTab('summary'); }, []); // eslint-disable-line

  const tabs: { id: ResearchTab; label: string }[] = [
    { id: 'summary', label: 'Research Summary' },
    { id: 'timing', label: 'Trade Timing' },
    { id: 'persistence', label: 'Alpha Persistence' },
    { id: 'heatmap', label: 'Market Heatmap' },
    { id: 'clusters', label: 'Wallet Clusters' },
    { id: 'experiment', label: 'Experiment Lab' },
    { id: 'matrix', label: 'Matrix Lab' },
    { id: 'wallet_intel', label: 'Wallet Intel' },
    { id: 'leader_impact', label: 'Leader Impact' },
  ];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-bold">Research Lab</h1>
        <p className="text-sm text-gray-500">
          Deep analysis: timing edge, alpha persistence, market inefficiency, and wallet clustering
        </p>
      </div>

      {/* Tab bar */}
      <div className="flex gap-1 border-b border-white/5 pb-1">
        {tabs.map((t) => (
          <button
            key={t.id}
            onClick={() => loadTab(t.id)}
            className={`rounded-t px-4 py-2 text-xs font-medium transition ${
              tab === t.id ? 'bg-white/10 text-white' : 'text-gray-400 hover:text-gray-200'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {loading && <p className="text-sm text-gray-500">Computing analysis...</p>}

      {/* RESEARCH SUMMARY */}
      {tab === 'summary' && summary && !loading && (
        <div className="space-y-6">
          <div className="rounded-xl border border-yellow-500/20 bg-surface-raised p-5">
            <h2 className="mb-1 text-sm font-semibold text-yellow-400">Combined Signal Leaderboard</h2>
            <p className="mb-4 text-xs text-gray-500">
              combined_signal = 0.5×information_score + 0.5×persistence_score. Top copy targets.
            </p>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="border-b border-white/5 text-left text-xs text-gray-500">
                  <tr>
                    <th className="py-2 pr-4">Wallet</th>
                    <th className="py-2 pr-4">Combined Signal</th>
                    <th className="py-2 pr-4">Info Score</th>
                    <th className="py-2 pr-4">Edge Window</th>
                    <th className="py-2 pr-4">Persistence</th>
                    <th className="py-2">Timing Verdict</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-white/5">
                  {summary.top_wallets.map((w) => (
                    <tr key={w.wallet_id} className="hover:bg-white/[0.02]">
                      <td className="py-2 pr-4">
                        <div className="font-medium">{w.label}</div>
                        <div className="font-mono text-[10px] text-gray-500">{w.address.slice(0, 14)}...</div>
                      </td>
                      <td className="py-2 pr-4">
                        <SignalBar value={w.combined_signal} />
                      </td>
                      <td className="py-2 pr-4">
                        <SignalBar value={w.information_score} />
                      </td>
                      <td className="py-2 pr-4 tabular-nums text-xs">
                        {w.edge_window_seconds > 0 ? `${w.edge_window_seconds}s` : '—'}
                      </td>
                      <td className="py-2 pr-4">
                        <SignalBar value={w.persistence_score} />
                      </td>
                      <td className="py-2">
                        <VerdictChip verdict={w.timing_verdict} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {summary.top_wallets.length === 0 && (
                <p className="py-6 text-center text-sm text-gray-500">
                  Not enough data yet. Need more tracked wallets with trade history.
                </p>
              )}
            </div>
          </div>

          <div className="rounded-xl border border-white/5 bg-surface-raised p-5">
            <h2 className="mb-4 text-sm font-semibold text-gray-300">Top Market Categories by Inefficiency</h2>
            <div className="space-y-2">
              {summary.heatmap_summary.map((c) => (
                <div key={c.category} className="flex items-center justify-between border-b border-white/5 py-2">
                  <div>
                    <span className="font-medium capitalize">{c.category}</span>
                    <span className="ml-2 text-xs text-gray-500">{c.market_count} markets</span>
                  </div>
                  <div className="flex items-center gap-4">
                    <SignalBar value={c.avg_inefficiency} label="inefficiency" />
                    <span className={`rounded px-2 py-0.5 text-[10px] ${
                      c.verdict.startsWith('HIGH') ? 'bg-green-500/20 text-green-400' :
                      c.verdict.startsWith('MODERATE') ? 'bg-yellow-500/20 text-yellow-400' :
                      'bg-gray-500/20 text-gray-400'
                    }`}>
                      {c.verdict.split(':')[0]}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* TRADE TIMING */}
      {tab === 'timing' && timing && !loading && (
        <div className="space-y-4">
          <div className="rounded-xl border border-white/5 bg-surface-raised p-4 text-xs text-gray-400">
            <strong className="text-gray-200">What this measures:</strong> Does the wallet trade BEFORE the market price moves?
            A high information_score ({">"} 0.5) means the wallet consistently precedes price changes — genuine information edge.
          </div>
          {timing.profiles.map((p) => (
            <div key={p.wallet_id} className="rounded-xl border border-white/5 bg-surface-raised p-5">
              <div className="mb-3 flex items-center justify-between">
                <div>
                  <span className="font-medium">{p.label}</span>
                  <span className="ml-2 font-mono text-xs text-gray-500">{p.address.slice(0, 14)}...</span>
                </div>
                <div className="flex items-center gap-3">
                  <span className="text-xs text-gray-500">{p.trade_count} trades</span>
                  <VerdictChip verdict={p.verdict} />
                </div>
              </div>
              <div className="mb-3 flex gap-6">
                <div>
                  <p className="text-xs text-gray-500">Information Score</p>
                  <SignalBar value={p.information_score} />
                </div>
                <div>
                  <p className="text-xs text-gray-500">Edge Window</p>
                  <p className="text-sm font-medium">{p.edge_window_seconds > 0 ? `${p.edge_window_seconds}s` : '—'}</p>
                </div>
              </div>
              {Object.keys(p.price_move_by_horizon).length > 0 && (
                <div className="mt-2">
                  <p className="mb-2 text-xs text-gray-500">Avg price move after trade (directional)</p>
                  <div className="flex gap-3">
                    {Object.entries(p.price_move_by_horizon).map(([horizon, data]) => (
                      <div key={horizon} className="text-center">
                        <p className={`text-sm font-bold tabular-nums ${data.avg_move > 0.002 ? 'text-green-400' : data.avg_move < -0.002 ? 'text-red-400' : 'text-gray-400'}`}>
                          {data.avg_move > 0 ? '+' : ''}{(data.avg_move * 100).toFixed(2)}%
                        </p>
                        <p className="text-[10px] text-gray-500">@{horizon}</p>
                        <p className="text-[10px] text-gray-600">{(data.pct_positive * 100).toFixed(0)}% pos</p>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {p.verdict === 'INSUFFICIENT_DATA' && (
                <p className="mt-2 text-xs text-gray-500">
                  ⏳ Yetersiz veri — wallet <strong className="text-gray-400">{p.trade_count}</strong> trade ile takip ediliyor.
                  Market snapshot eşleşmesi için birkaç ingestion döngüsü gerekiyor.
                </p>
              )}
            </div>
          ))}
          {timing.profiles.length === 0 && <p className="text-sm text-gray-500">No profiles available.</p>}
        </div>
      )}

      {/* ALPHA PERSISTENCE */}
      {tab === 'persistence' && persistence && !loading && (
        <div className="space-y-4">
          <div className="rounded-xl border border-white/5 bg-surface-raised p-4 text-xs text-gray-400">
            <strong className="text-gray-200">What this measures:</strong> Does a wallet's weekly performance correlate with its previous week?
            persistence_score {">"} 0.6 = real strategy. {"<"} 0.5 = likely luck.
          </div>
          {persistence.profiles.map((p) => (
            <div key={p.wallet_id} className="rounded-xl border border-white/5 bg-surface-raised p-5">
              <div className="mb-3 flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="font-medium">{p.label}</span>
                  {p.is_real && <span className="rounded bg-green-500/10 px-1.5 py-0.5 text-[10px] text-green-400">LIVE</span>}
                </div>
                <VerdictChip verdict={p.verdict} />
              </div>
              <div className="mb-3 flex gap-6">
                <div>
                  <p className="text-xs text-gray-500">Persistence Score</p>
                  <SignalBar value={p.persistence_score} />
                </div>
                <div>
                  <p className="text-xs text-gray-500">Autocorrelation</p>
                  <p className={`text-sm font-medium ${p.autocorrelation > 0.2 ? 'text-green-400' : p.autocorrelation < -0.2 ? 'text-red-400' : 'text-gray-400'}`}>
                    {p.autocorrelation.toFixed(3)}
                  </p>
                </div>
                <div>
                  <p className="text-xs text-gray-500">Windows</p>
                  <p className="text-sm">{p.total_windows}</p>
                </div>
              </div>
              {p.windows.length > 1 && (
                <div className="flex h-12 items-end gap-0.5">
                  {p.windows.map((w, i) => {
                    const maxAbs = Math.max(...p.windows.map(x => Math.abs(x.roi_proxy)), 0.001);
                    const h = Math.abs(w.roi_proxy) / maxAbs * 40;
                    return (
                      <div
                        key={i}
                        className={`flex-1 rounded-sm ${w.roi_proxy >= 0 ? 'bg-green-500/60' : 'bg-red-500/60'}`}
                        style={{ height: `${Math.max(2, h)}px` }}
                        title={`Week ${i + 1}: roi=${(w.roi_proxy * 100).toFixed(2)}%`}
                      />
                    );
                  })}
                </div>
              )}
              {p.verdict === 'INSUFFICIENT_DATA' && (
                <p className="mt-2 text-xs text-gray-500">
                  ⏳ Yetersiz veri — <strong className="text-gray-400">{p.trade_count}</strong> trade mevcut. Alpha persistence için en az 2 haftalık trade geçmişi gerekiyor.
                </p>
              )}
            </div>
          ))}
        </div>
      )}

      {/* MARKET HEATMAP */}
      {tab === 'heatmap' && heatmap && !loading && (
        <div className="space-y-4">
          <div className="rounded-xl border border-white/5 bg-surface-raised p-5">
            <h2 className="mb-4 text-sm font-semibold text-gray-300">Inefficiency by Category</h2>
            <div className="space-y-3">
              {heatmap.categories.map((c) => (
                <div key={c.category} className="flex items-center gap-4">
                  <div className="w-24 capitalize text-sm font-medium">{c.category}</div>
                  <div className="flex-1">
                    <div className="h-6 rounded bg-white/5 relative overflow-hidden">
                      <div
                        className={`h-full rounded transition-all ${
                          c.avg_inefficiency > 0.4 ? 'bg-green-500/60' :
                          c.avg_inefficiency > 0.2 ? 'bg-yellow-500/60' : 'bg-red-500/30'
                        }`}
                        style={{ width: `${Math.min(c.avg_inefficiency * 100, 100)}%` }}
                      />
                      <span className="absolute inset-y-0 right-2 flex items-center text-xs text-gray-300">
                        {(c.avg_inefficiency * 100).toFixed(1)}%
                      </span>
                    </div>
                  </div>
                  <div className="w-32 text-xs text-gray-500">{c.market_count} markets</div>
                  <span className={`rounded px-2 py-0.5 text-[10px] ${
                    c.verdict.startsWith('HIGH') ? 'bg-green-500/20 text-green-400' :
                    c.verdict.startsWith('MODERATE') ? 'bg-yellow-500/20 text-yellow-400' :
                    'bg-gray-500/20 text-gray-400'
                  }`}>
                    {c.verdict.split(':')[0]}
                  </span>
                </div>
              ))}
            </div>
          </div>

          <div className="rounded-xl border border-white/5 bg-surface-raised p-5">
            <h2 className="mb-3 text-sm font-semibold text-gray-300">Top 20 Most Inefficient Individual Markets</h2>
            <div className="space-y-1">
              {heatmap.top_markets.slice(0, 10).map((m, i) => (
                <div key={m.market_id} className="flex items-center gap-3 border-b border-white/5 py-1.5 text-xs">
                  <span className="w-4 text-gray-600">{i + 1}</span>
                  <span className="flex-1 truncate text-gray-300">{m.question}</span>
                  <span className="rounded bg-white/5 px-1.5 capitalize">{m.category}</span>
                  <span className={`font-mono ${m.composite_score > 0.4 ? 'text-green-400' : 'text-gray-400'}`}>
                    {(m.composite_score * 100).toFixed(1)}%
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* WALLET CLUSTERS */}
      {tab === 'clusters' && clusters && !loading && (
        <div className="space-y-4">
          <div className="grid grid-cols-3 gap-4">
            <div className="rounded-xl border border-white/5 bg-surface-raised p-4">
              <p className="text-xs text-gray-500">Total Wallets</p>
              <p className="mt-1 text-2xl font-bold">{clusters.total_wallets}</p>
            </div>
            <div className="rounded-xl border border-white/5 bg-surface-raised p-4">
              <p className="text-xs text-gray-500">Potential Clusters</p>
              <p className="mt-1 text-2xl font-bold text-yellow-400">{clusters.clusters.length}</p>
            </div>
            <div className="rounded-xl border border-white/5 bg-surface-raised p-4">
              <p className="text-xs text-gray-500">Clustered Wallets</p>
              <p className="mt-1 text-2xl font-bold text-orange-400">{clusters.clustered_wallets}</p>
            </div>
          </div>

          <div className="rounded-xl border border-white/5 bg-surface-raised p-4 text-xs text-gray-400">
            <strong className="text-gray-200">What this measures:</strong> {clusters.insight}
          </div>

          {clusters.clusters.length > 0 ? (
            clusters.clusters.map((c) => (
              <div key={c.cluster_id} className="rounded-xl border border-orange-500/20 bg-surface-raised p-5">
                <div className="mb-3 flex items-center justify-between">
                  <h3 className="font-medium text-orange-400">{c.cluster_id.replace('_', ' ').toUpperCase()}</h3>
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-gray-500">similarity: {(c.avg_similarity * 100).toFixed(0)}%</span>
                  </div>
                </div>
                <p className="mb-3 text-xs text-orange-300">{c.warning.split(':')[0]}</p>
                <div className="mb-3 flex flex-wrap gap-2">
                  {c.members.map((m) => (
                    <div key={m.wallet_id} className="rounded border border-white/10 px-2 py-1">
                      <div className="text-xs font-medium">{m.label}</div>
                      <div className="font-mono text-[10px] text-gray-500">{m.address.slice(0, 12)}...</div>
                    </div>
                  ))}
                </div>
                {c.evidence.length > 0 && (
                  <div>
                    <p className="text-xs text-gray-500">Evidence:</p>
                    <ul className="mt-1 space-y-0.5">
                      {c.evidence.map((e, i) => (
                        <li key={i} className="text-xs text-gray-400">• {e}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            ))
          ) : (
            <div className="rounded-xl border border-white/5 bg-surface-raised p-8 text-center">
              <p className="text-sm text-gray-400">No significant clusters detected.</p>
              <p className="mt-1 text-xs text-gray-500">All tracked wallets appear to be independent traders.</p>
            </div>
          )}
        </div>
      )}

      {/* ── Experiment Lab ────────────────────────────────────────────── */}
      {tab === 'experiment' && (
        <div className="space-y-6">
          <div className="rounded-xl border border-white/5 bg-surface-raised p-5">
            <h2 className="mb-1 font-semibold text-white">Experiment Lab</h2>
            <p className="text-xs text-gray-400">
              Adjust filter parameters and run a what-if simulation against the current market
              snapshot. The system re-evaluates every tracked pair without modifying any
              positions.
            </p>
          </div>

          {/* Parameter sliders */}
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {/* Min z-score */}
            <div className="rounded-xl border border-white/5 bg-surface-raised p-4">
              <label className="block text-xs font-medium text-gray-400">Min z-score</label>
              <div className="mt-2 flex items-center gap-3">
                <input
                  type="range" min="0.5" max="6" step="0.5"
                  value={expParams.min_z}
                  onChange={(e) => setExpParams((p) => ({ ...p, min_z: parseFloat(e.target.value) }))}
                  className="w-full accent-cyan-500"
                />
                <span className="w-10 text-right font-mono text-sm text-cyan-400">{expParams.min_z.toFixed(1)}</span>
              </div>
              <p className="mt-1 text-[10px] text-gray-600">Currently active: 2.0</p>
            </div>

            {/* Min depth */}
            <div className="rounded-xl border border-white/5 bg-surface-raised p-4">
              <label className="block text-xs font-medium text-gray-400">Min Depth ($)</label>
              <div className="mt-2 flex items-center gap-3">
                <input
                  type="range" min="10" max="1000" step="10"
                  value={expParams.min_depth}
                  onChange={(e) => setExpParams((p) => ({ ...p, min_depth: parseFloat(e.target.value) }))}
                  className="w-full accent-cyan-500"
                />
                <span className="w-14 text-right font-mono text-sm text-cyan-400">${expParams.min_depth}</span>
              </div>
              <p className="mt-1 text-[10px] text-gray-600">Currently active: $20</p>
            </div>

            {/* Max spread */}
            <div className="rounded-xl border border-white/5 bg-surface-raised p-4">
              <label className="block text-xs font-medium text-gray-400">Max Spread (%)</label>
              <div className="mt-2 flex items-center gap-3">
                <input
                  type="range" min="2" max="30" step="1"
                  value={Math.round(expParams.max_spread_pct * 100)}
                  onChange={(e) => setExpParams((p) => ({ ...p, max_spread_pct: parseFloat(e.target.value) / 100 }))}
                  className="w-full accent-cyan-500"
                />
                <span className="w-12 text-right font-mono text-sm text-cyan-400">{(expParams.max_spread_pct * 100).toFixed(0)}%</span>
              </div>
              <p className="mt-1 text-[10px] text-gray-600">Currently active: 20%</p>
            </div>

            {/* Min conf edge */}
            <div className="rounded-xl border border-white/5 bg-surface-raised p-4">
              <label className="block text-xs font-medium text-gray-400">Min Conf. Edge</label>
              <div className="mt-2 flex items-center gap-3">
                <input
                  type="range" min="0" max="0.1" step="0.005"
                  value={expParams.min_conf_edge}
                  onChange={(e) => setExpParams((p) => ({ ...p, min_conf_edge: parseFloat(e.target.value) }))}
                  className="w-full accent-cyan-500"
                />
                <span className="w-14 text-right font-mono text-sm text-cyan-400">{expParams.min_conf_edge.toFixed(3)}</span>
              </div>
              <p className="mt-1 text-[10px] text-gray-600">Currently active: 0.005</p>
            </div>
          </div>

          {/* Quick presets */}
          <div className="flex flex-wrap gap-2">
            <span className="self-center text-xs text-gray-500">Presets:</span>
            {[
              { label: 'Current defaults', p: { min_z: 2.0, min_depth: 20, max_spread_pct: 0.20, min_conf_edge: 0.005 } },
              { label: 'Conservative', p: { min_z: 3.5, min_depth: 500, max_spread_pct: 0.06, min_conf_edge: 0.03 } },
              { label: 'Aggressive', p: { min_z: 1.5, min_depth: 10, max_spread_pct: 0.25, min_conf_edge: 0.001 } },
              { label: 'Experiment 1 (blog)', p: { min_z: 2.0, min_depth: 20, max_spread_pct: 0.12, min_conf_edge: 0.03 } },
            ].map((preset) => (
              <button
                key={preset.label}
                onClick={() => setExpParams(preset.p)}
                className="rounded border border-white/10 px-3 py-1 text-xs text-gray-300 hover:border-cyan-500/50 hover:text-cyan-400 transition-colors"
              >
                {preset.label}
              </button>
            ))}
          </div>

          {/* Run button */}
          <button
            onClick={async () => {
              setExpLoading(true);
              try {
                const qs = new URLSearchParams({
                  min_conf_edge: expParams.min_conf_edge.toString(),
                  min_depth: expParams.min_depth.toString(),
                  min_z: expParams.min_z.toString(),
                  max_spread_pct: expParams.max_spread_pct.toString(),
                });
                const d = await apiFetch<ExperimentResult>(`/api/markets/experiment?${qs}`);
                setExpResult(d);
              } catch (e) {
                console.error(e);
              } finally {
                setExpLoading(false);
              }
            }}
            disabled={expLoading}
            className="rounded-xl bg-cyan-600 px-6 py-2.5 text-sm font-semibold text-white hover:bg-cyan-500 disabled:opacity-50 transition-colors"
          >
            {expLoading ? 'Running...' : 'Run Experiment'}
          </button>

          {/* Results */}
          {expResult && (
            <div className="space-y-4">
              {/* Gate funnel */}
              <div className="rounded-xl border border-white/5 bg-surface-raised p-5">
                <h3 className="mb-4 font-medium text-white">Filter Funnel (current snapshot)</h3>
                <div className="space-y-3">
                  {[
                    { label: 'All pairs monitored', value: expResult.current_snapshot.total_pairs, color: 'bg-gray-500' },
                    { label: `Pass z >= ${expResult.params.min_z}`, value: expResult.current_snapshot.passes_z, color: 'bg-blue-500' },
                    { label: `Pass depth >= $${expResult.params.min_depth}`, value: expResult.current_snapshot.passes_depth, color: 'bg-indigo-500' },
                    { label: `Pass spread < ${(expResult.params.max_spread_pct * 100).toFixed(0)}%`, value: expResult.current_snapshot.passes_spread, color: 'bg-violet-500' },
                    { label: `Pass conf. edge >= ${expResult.params.min_conf_edge.toFixed(3)}`, value: expResult.current_snapshot.passes_edge, color: 'bg-cyan-500' },
                    { label: 'Would trade NOW', value: expResult.current_snapshot.would_trade_now, color: 'bg-emerald-500' },
                  ].map((row) => {
                    const max = expResult.current_snapshot.total_pairs || 1;
                    const pct = (row.value / max) * 100;
                    return (
                      <div key={row.label} className="flex items-center gap-3">
                        <div className="w-56 shrink-0 text-xs text-gray-400">{row.label}</div>
                        <div className="flex-1">
                          <div className="h-2 w-full rounded-full bg-white/5">
                            <div
                              className={`h-full rounded-full ${row.color} transition-all`}
                              style={{ width: `${pct}%` }}
                            />
                          </div>
                        </div>
                        <div className="w-8 text-right font-mono text-xs text-white">{row.value}</div>
                      </div>
                    );
                  })}
                </div>
              </div>

              {/* Historical precision panel */}
              <div className="rounded-xl border border-white/5 bg-surface-raised p-5">
                <h3 className="mb-3 font-medium text-white">Historical Precision (closed dislocation trades)</h3>
                <div className="flex flex-wrap gap-6">
                  <div>
                    <p className="text-xs text-gray-500">Profitable / Total</p>
                    <p className="mt-1 text-xl font-bold text-white">
                      {expResult.historical_precision.profitable} / {expResult.historical_precision.total_closed}
                    </p>
                  </div>
                  <div>
                    <p className="text-xs text-gray-500">Precision</p>
                    <p className={`mt-1 text-xl font-bold ${
                      (expResult.historical_precision.precision ?? 0) >= 0.6 ? 'text-emerald-400' :
                      (expResult.historical_precision.precision ?? 0) >= 0.4 ? 'text-yellow-400' : 'text-red-400'
                    }`}>
                      {expResult.historical_precision.precision !== null
                        ? `${(expResult.historical_precision.precision * 100).toFixed(1)}%`
                        : 'N/A'}
                    </p>
                  </div>
                  <div>
                    <p className="text-xs text-gray-500">Grade</p>
                    <p className={`mt-1 text-xl font-bold ${
                      (expResult.historical_precision.precision ?? 0) >= 0.7 ? 'text-emerald-400' :
                      (expResult.historical_precision.precision ?? 0) >= 0.55 ? 'text-yellow-400' :
                      (expResult.historical_precision.precision ?? 0) >= 0.4 ? 'text-orange-400' : 'text-red-400'
                    }`}>
                      {expResult.historical_precision.precision === null ? 'N/A' :
                        expResult.historical_precision.precision >= 0.7 ? 'A' :
                        expResult.historical_precision.precision >= 0.55 ? 'B' :
                        expResult.historical_precision.precision >= 0.4 ? 'C' : 'D'}
                    </p>
                  </div>
                </div>
                <p className="mt-3 text-[11px] text-gray-500">
                  Note: precision is based on all-time closed dislocation positions and does not change with
                  parameter sweep. Use it as a baseline when comparing experiments.
                </p>
              </div>

              {/* Per-pair gate table */}
              {expResult.gate_breakdown.length > 0 && (
                <div className="rounded-xl border border-white/5 bg-surface-raised overflow-hidden">
                  <div className="px-5 py-3 border-b border-white/5">
                    <h3 className="font-medium text-white text-sm">Per-Pair Gate Breakdown</h3>
                  </div>
                  <div className="overflow-x-auto">
                    <table className="w-full text-xs">
                      <thead>
                        <tr className="border-b border-white/5 text-left text-[10px] text-gray-500">
                          <th className="px-4 py-2">Pair</th>
                          <th className="px-3 py-2 text-right">z-score</th>
                          <th className="px-3 py-2 text-right">Depth $</th>
                          <th className="px-3 py-2 text-right">Spread %</th>
                          <th className="px-3 py-2 text-right">Conf. Edge</th>
                          <th className="px-3 py-2 text-center">z</th>
                          <th className="px-3 py-2 text-center">Depth</th>
                          <th className="px-3 py-2 text-center">Spread</th>
                          <th className="px-3 py-2 text-center">Edge</th>
                          <th className="px-3 py-2 text-center">Trade?</th>
                        </tr>
                      </thead>
                      <tbody>
                        {expResult.gate_breakdown.map((row, i) => (
                          <tr key={i} className={`border-b border-white/5 ${row.would_trade ? 'bg-emerald-500/5' : ''}`}>
                            <td className="px-4 py-2 text-gray-300">{row.pair}</td>
                            <td className="px-3 py-2 text-right font-mono tabular-nums text-gray-300">{row.z.toFixed(2)}</td>
                            <td className="px-3 py-2 text-right font-mono tabular-nums text-gray-400">{row.min_depth.toFixed(0)}</td>
                            <td className="px-3 py-2 text-right font-mono tabular-nums text-gray-400">{row.spread_pct.toFixed(1)}%</td>
                            <td className={`px-3 py-2 text-right font-mono tabular-nums ${row.conf_adj_edge > 0 ? 'text-cyan-400' : 'text-red-400'}`}>
                              {row.conf_adj_edge.toFixed(4)}
                            </td>
                            {[row.ok_z, row.ok_depth, row.ok_spread, row.ok_edge].map((ok, j) => (
                              <td key={j} className="px-3 py-2 text-center">
                                <span className={ok ? 'text-emerald-400' : 'text-red-500'}>
                                  {ok ? 'OK' : '--'}
                                </span>
                              </td>
                            ))}
                            <td className="px-3 py-2 text-center">
                              {row.would_trade
                                ? <span className="rounded bg-emerald-500/20 px-1.5 py-0.5 text-[10px] font-semibold text-emerald-400">YES</span>
                                : <span className="text-gray-600">no</span>}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* MATRIX LAB */}
      {tab === 'matrix' && (
        <div className="space-y-6">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-sm font-semibold text-white">Parameter Matrix</h2>
              <p className="text-xs text-gray-500 mt-0.5">
                3-axis sweep: conf_edge x spread_ratio x depth. Sweet spot = highest EV score.
              </p>
            </div>
            <button
              onClick={loadMatrix}
              disabled={matrixLoading}
              className="rounded-lg bg-violet-600 px-4 py-2 text-xs font-medium text-white hover:bg-violet-500 disabled:opacity-50"
            >
              {matrixLoading ? 'Computing...' : 'Refresh Matrix'}
            </button>
          </div>

          {matrixResult && (
            <>
              {/* Header stats */}
              <div className="grid grid-cols-3 gap-3">
                <div className="rounded-xl border border-white/5 bg-surface-raised p-4">
                  <p className="text-[10px] text-gray-500 uppercase tracking-wide">Pairs Monitored</p>
                  <p className="text-2xl font-bold text-white mt-1">{matrixResult.total_pairs_monitored}</p>
                </div>
                <div className="rounded-xl border border-white/5 bg-surface-raised p-4">
                  <p className="text-[10px] text-gray-500 uppercase tracking-wide">Baseline Precision</p>
                  <p className={`text-2xl font-bold mt-1 ${
                    matrixResult.hist_baseline_precision >= 0.6 ? 'text-emerald-400' :
                    matrixResult.hist_baseline_precision >= 0.45 ? 'text-yellow-400' : 'text-red-400'
                  }`}>
                    {(matrixResult.hist_baseline_precision * 100).toFixed(0)}%
                  </p>
                  <p className="text-[10px] text-gray-500 mt-0.5">from {matrixResult.hist_total_trades} closed trades</p>
                </div>
                <div className="rounded-xl border border-white/5 bg-surface-raised p-4">
                  <p className="text-[10px] text-gray-500 uppercase tracking-wide">Sweet Spot Found</p>
                  {matrixResult.sweet_spot ? (
                    <>
                      <p className="text-emerald-400 font-bold text-lg mt-1">Yes</p>
                      <p className="text-[10px] text-gray-400">
                        ce={matrixResult.sweet_spot.conf_edge} / ratio={matrixResult.sweet_spot.spread_ratio} / d=${matrixResult.sweet_spot.depth}
                      </p>
                    </>
                  ) : (
                    <p className="text-red-400 font-bold text-sm mt-1">None found yet</p>
                  )}
                </div>
              </div>

              {/* Sweet spot detail */}
              {matrixResult.sweet_spot && (
                <div className="rounded-xl border border-violet-500/30 bg-violet-500/5 p-5">
                  <h3 className="text-sm font-semibold text-violet-300 mb-3">Optimal Parameter Set</h3>
                  <div className="grid grid-cols-3 gap-4 text-sm">
                    {[
                      { label: 'Min Conf. Edge', value: String(matrixResult.sweet_spot.conf_edge) },
                      { label: 'Max Spread/Edge', value: String(matrixResult.sweet_spot.spread_ratio) },
                      { label: 'Min Depth', value: `$${matrixResult.sweet_spot.depth}` },
                      { label: 'Would Trade Now', value: `${matrixResult.sweet_spot.would_trade_now} pairs`, color: matrixResult.sweet_spot.would_trade_now > 0 ? 'text-emerald-400' : 'text-gray-400' },
                      { label: 'Est. Precision', value: `${(matrixResult.sweet_spot.est_precision * 100).toFixed(0)}%`, color: matrixResult.sweet_spot.est_precision >= 0.6 ? 'text-emerald-400' : 'text-yellow-400' },
                      { label: 'EV Score', value: matrixResult.sweet_spot.ev_score.toFixed(4), color: 'text-violet-300' },
                    ].map(({ label, value, color }) => (
                      <div key={label}>
                        <p className="text-[10px] text-gray-500">{label}</p>
                        <p className={`font-mono font-bold ${color || 'text-white'}`}>{value}</p>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Depth filter */}
              <div className="flex items-center gap-4">
                <p className="text-xs text-gray-400">Show depth:</p>
                {matrixResult.axes.depth_values.map((d) => (
                  <button
                    key={d}
                    onClick={() => setMatrixDepthFilter(d)}
                    className={`rounded px-3 py-1 text-xs font-medium transition ${
                      matrixDepthFilter === d ? 'bg-violet-600 text-white' : 'bg-white/5 text-gray-400 hover:bg-white/10'
                    }`}
                  >
                    ${d}
                  </button>
                ))}
              </div>

              {/* Matrix grid */}
              <div className="rounded-xl border border-white/5 bg-surface-raised overflow-hidden">
                <div className="px-5 py-3 border-b border-white/5">
                  <h3 className="font-medium text-white text-sm">
                    Sweep Grid — Depth ${matrixDepthFilter}
                    <span className="ml-2 text-[10px] text-gray-500">rows=conf_edge / cols=spread_ratio</span>
                  </h3>
                </div>
                <div className="overflow-x-auto p-4">
                  <table className="text-xs">
                    <thead>
                      <tr>
                        <th className="pr-4 pb-2 text-left text-[10px] text-gray-500">ce \\ ratio</th>
                        {matrixResult.axes.spread_ratio_values.map((sr) => (
                          <th key={sr} className="px-4 pb-2 text-center text-[10px] text-gray-500">&le;{sr}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {matrixResult.axes.conf_edge_values.map((ce) => (
                        <tr key={ce} className="border-t border-white/5">
                          <td className="pr-4 py-2 font-mono text-gray-400 text-[10px]">{ce}</td>
                          {matrixResult.axes.spread_ratio_values.map((sr) => {
                            const cell = matrixResult.matrix.find(
                              (c) => c.conf_edge === ce && c.spread_ratio === sr && c.depth === matrixDepthFilter
                            );
                            if (!cell) return <td key={sr} className="px-4 py-2 text-center text-gray-700">-</td>;
                            const cellBg = cell.is_sweet_spot
                              ? 'bg-violet-500/20 ring-1 ring-violet-500/50'
                              : cell.would_trade_now > 0
                              ? 'bg-emerald-500/5'
                              : '';
                            return (
                              <td key={sr} className={`px-4 py-2 text-center rounded ${cellBg}`}>
                                <div className={`font-bold text-sm ${cell.would_trade_now > 0 ? 'text-emerald-400' : 'text-gray-600'}`}>
                                  {cell.would_trade_now}
                                </div>
                                <div className="text-[9px] text-gray-500">
                                  {(cell.est_precision * 100).toFixed(0)}%
                                </div>
                                {cell.is_sweet_spot && <div className="text-[8px] text-violet-400 font-bold">BEST</div>}
                              </td>
                            );
                          })}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <p className="mt-2 text-[10px] text-gray-600">Cell: trade count (top) / est. precision (bottom). Violet = sweet spot.</p>
                </div>
              </div>

              {/* Top cells table */}
              <div className="rounded-xl border border-white/5 bg-surface-raised overflow-hidden">
                <div className="px-5 py-3 border-b border-white/5">
                  <h3 className="font-medium text-white text-sm">Top Cells by EV Score</h3>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="border-b border-white/5 text-left text-[10px] text-gray-500">
                        <th className="px-4 py-2">Conf Edge</th>
                        <th className="px-3 py-2">Ratio</th>
                        <th className="px-3 py-2">Depth</th>
                        <th className="px-3 py-2 text-right">Trade Now</th>
                        <th className="px-3 py-2 text-right">Est. Precision</th>
                        <th className="px-3 py-2 text-right">EV Score</th>
                        <th className="px-3 py-2"></th>
                      </tr>
                    </thead>
                    <tbody>
                      {[...matrixResult.matrix]
                        .sort((a, b) => b.ev_score - a.ev_score)
                        .filter((c) => c.would_trade_now > 0 || c.is_sweet_spot)
                        .slice(0, 15)
                        .map((cell, i) => (
                          <tr key={i} className={`border-b border-white/5 ${cell.is_sweet_spot ? 'bg-violet-500/10' : 'hover:bg-white/[0.02]'}`}>
                            <td className="px-4 py-2 font-mono text-gray-300">{cell.conf_edge}</td>
                            <td className="px-3 py-2 font-mono text-gray-300">{cell.spread_ratio}</td>
                            <td className="px-3 py-2 text-gray-400">${cell.depth}</td>
                            <td className={`px-3 py-2 text-right font-bold ${cell.would_trade_now > 0 ? 'text-emerald-400' : 'text-gray-600'}`}>
                              {cell.would_trade_now}
                            </td>
                            <td className={`px-3 py-2 text-right ${cell.est_precision >= 0.6 ? 'text-emerald-400' : 'text-yellow-400'}`}>
                              {(cell.est_precision * 100).toFixed(0)}%
                            </td>
                            <td className="px-3 py-2 text-right font-mono text-violet-300">{cell.ev_score.toFixed(4)}</td>
                            <td className="px-3 py-2 text-center">
                              {cell.is_sweet_spot && <span className="rounded bg-violet-500/20 px-2 py-0.5 text-[9px] text-violet-300 font-bold">SWEET SPOT</span>}
                            </td>
                          </tr>
                        ))}
                      {matrixResult.matrix.filter((c) => c.would_trade_now > 0).length === 0 && (
                        <tr>
                          <td colSpan={7} className="px-4 py-6 text-center text-xs text-gray-500">
                            No cells with active trades at current market conditions.
                            <br />System needs more active dislocations to find the sweet spot.
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
              </div>
            </>
          )}

          {!matrixResult && !matrixLoading && (
            <div className="rounded-xl border border-white/5 bg-surface-raised p-8 text-center">
              <p className="text-sm text-gray-400 mb-3">Run the parameter matrix sweep to find optimal filter settings.</p>
              <button onClick={loadMatrix} className="rounded-lg bg-violet-600 px-4 py-2 text-xs font-medium text-white hover:bg-violet-500">
                Run Matrix
              </button>
            </div>
          )}
          {matrixLoading && (
            <div className="rounded-xl border border-white/5 bg-surface-raised p-8 text-center">
              <p className="text-sm text-gray-500">Running 80-cell parameter matrix...</p>
            </div>
          )}
        </div>
      )}

      {/* ── Wallet Intelligence Tab ────────────────────────────────── */}
      {tab === 'wallet_intel' && (
        <div className="space-y-6">
          <div className="rounded-xl border border-violet-500/20 bg-surface-raised p-5">
            <h2 className="text-sm font-semibold text-gray-300">
              Wallet Intelligence Engine
              <span className="ml-2 rounded bg-violet-500/20 px-1.5 py-0.5 text-[10px] text-violet-400">INFORMATION ALPHA</span>
            </h2>
            <p className="mt-1 text-xs text-gray-500">
              copyable_alpha_score = timing × persistence × vol-adjusted ROI × latency survivability × drawdown stability × information impact
            </p>
          </div>

          {walletIntelLoading && (
            <div className="rounded-xl border border-white/5 bg-surface-raised p-8 text-center">
              <p className="text-sm text-gray-500">Computing 6-factor alpha scores for all tracked wallets...</p>
            </div>
          )}

          {/* Alpha Leaderboard */}
          {alphaLeaderboard && (
            <div className="rounded-xl border border-white/5 bg-surface-raised p-5">
              <div className="mb-4 flex items-center justify-between">
                <div>
                  <h3 className="text-sm font-semibold text-gray-300">Copyable Alpha Leaderboard</h3>
                  <p className="mt-0.5 text-xs text-gray-500">
                    {alphaLeaderboard.total_evaluated} wallets evaluated —
                    <span className="ml-1 text-emerald-400">{alphaLeaderboard.copy_now_count} copy_now</span>
                    <span className="ml-1 text-yellow-400">{alphaLeaderboard.monitor_count} monitor</span>
                    <span className="ml-1 text-red-400">{alphaLeaderboard.avoid_count} avoid</span>
                  </p>
                </div>
              </div>

              <div className="space-y-2">
                {alphaLeaderboard.leaderboard.map((w) => {
                  const recColor =
                    w.recommendation === 'copy_now' ? 'bg-emerald-500/10 text-emerald-400' :
                    w.recommendation === 'monitor'  ? 'bg-yellow-500/10 text-yellow-400' :
                    'bg-red-500/10 text-red-400';
                  const decayColor =
                    w.alpha_decay_risk === 'low'      ? 'text-emerald-400' :
                    w.alpha_decay_risk === 'medium'   ? 'text-yellow-400' :
                    w.alpha_decay_risk === 'high'     ? 'text-orange-400' : 'text-red-400';
                  return (
                    <div key={w.wallet_id} className="rounded-lg border border-white/5 p-3">
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-2">
                          <span className="font-mono text-xs text-gray-400">{w.address}</span>
                          {w.label && <span className="text-[10px] text-gray-500">({w.label})</span>}
                          <span className={`rounded px-1.5 py-0.5 text-[9px] font-bold uppercase ${recColor}`}>
                            {w.recommendation.replace('_', ' ')}
                          </span>
                        </div>
                        <div className="flex items-center gap-3">
                          <span className={`text-[10px] ${decayColor}`}>decay: {w.alpha_decay_risk}</span>
                          <span className="text-sm font-bold tabular-nums text-white">
                            {(w.copyable_alpha_score * 100).toFixed(1)}
                          </span>
                        </div>
                      </div>
                      {/* Factor bars */}
                      <div className="mt-2 grid grid-cols-4 gap-2 text-[10px]">
                        {Object.entries(w.top_factors).map(([k, v]) => (
                          <div key={k}>
                            <p className="text-gray-600 truncate">{k.replace('_', ' ')}</p>
                            <div className="mt-0.5 h-1 w-full rounded-full bg-white/5">
                              <div
                                className={`h-full rounded-full ${v >= 0.6 ? 'bg-emerald-500' : v >= 0.35 ? 'bg-yellow-500' : 'bg-red-500'}`}
                                style={{ width: `${Math.min(100, v * 100)}%` }}
                              />
                            </div>
                          </div>
                        ))}
                      </div>
                      {w.alpha_decay_risk !== 'low' && (
                        <p className="mt-1.5 text-[10px] text-orange-400">{w.decay_signal}</p>
                      )}
                    </div>
                  );
                })}
                {alphaLeaderboard.leaderboard.length === 0 && (
                  <p className="text-sm text-gray-500">No wallet data yet. Wallets need at least 3 transactions to score.</p>
                )}
              </div>
            </div>
          )}

          {/* Influence Graph */}
          {influenceGraph && (
            <div className="rounded-xl border border-white/5 bg-surface-raised p-5">
              <div className="mb-4">
                <h3 className="text-sm font-semibold text-gray-300">
                  Wallet Influence Graph
                  <span className="ml-2 rounded bg-blue-500/20 px-1.5 py-0.5 text-[10px] text-blue-400">INFORMATION FLOW</span>
                </h3>
                <p className="mt-1 text-xs text-gray-500">
                  Who trades first? Edges show: Wallet A trades → price moves → Wallet B follows.
                </p>
              </div>

              {/* Summary chips */}
              <div className="mb-4 flex flex-wrap gap-2 text-xs">
                <span className="rounded bg-white/5 px-2 py-1">{influenceGraph.node_count} wallets</span>
                <span className="rounded bg-white/5 px-2 py-1">{influenceGraph.edge_count} influence edges</span>
                <span className="rounded bg-emerald-500/10 px-2 py-1 text-emerald-400">{influenceGraph.leader_count} leaders</span>
                <span className="rounded bg-blue-500/10 px-2 py-1 text-blue-400">{influenceGraph.follower_count} followers</span>
              </div>

              {/* Leaders */}
              {influenceGraph.leaders.length > 0 && (
                <div className="mb-4 rounded-lg bg-emerald-500/5 border border-emerald-500/20 p-3">
                  <p className="text-xs font-semibold text-emerald-400 mb-2">Information Leaders</p>
                  <p className="text-[10px] text-gray-400 mb-2">These wallets consistently trade before others react. High priority for copying.</p>
                  <div className="flex flex-wrap gap-1">
                    {influenceGraph.leaders.map((l) => (
                      <span key={l} className="rounded bg-emerald-500/10 px-2 py-0.5 font-mono text-[10px] text-emerald-300">{l.slice(0, 12)}...</span>
                    ))}
                  </div>
                </div>
              )}

              {/* Top influence edges table */}
              {influenceGraph.top_edges.length > 0 && (
                <div className="overflow-x-auto">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="border-b border-white/5 text-gray-500">
                        <th className="py-2 text-left">Leader</th>
                        <th className="py-2 text-left">Follower</th>
                        <th className="py-2 text-right">Lag</th>
                        <th className="py-2 text-right">Obs</th>
                        <th className="py-2 text-right">Agree</th>
                        <th className="py-2 text-right">Weight</th>
                      </tr>
                    </thead>
                    <tbody>
                      {influenceGraph.top_edges.map((e, i) => (
                        <tr key={i} className="border-b border-white/5">
                          <td className="py-1.5 font-mono text-emerald-400">{e.leader}</td>
                          <td className="py-1.5 font-mono text-blue-400">{e.follower}</td>
                          <td className="py-1.5 text-right text-gray-300">{e.mean_lag_seconds.toFixed(0)}s</td>
                          <td className="py-1.5 text-right text-gray-300">{e.trade_count}</td>
                          <td className="py-1.5 text-right text-gray-300">{(e.outcome_correlation * 100).toFixed(0)}%</td>
                          <td className="py-1.5 text-right font-bold text-white">{e.weight.toFixed(2)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {influenceGraph.edge_count === 0 && (
                <p className="text-sm text-gray-500">
                  No influence patterns detected yet. Need multiple wallets trading the same markets within 10-minute windows.
                  Keep accumulating data.
                </p>
              )}

              <p className="mt-3 text-[10px] text-gray-600">
                Computed at {new Date(influenceGraph.computed_at).toLocaleTimeString('tr-TR')}
              </p>
            </div>
          )}

          {!alphaLeaderboard && !walletIntelLoading && (
            <div className="rounded-xl border border-white/5 bg-surface-raised p-8 text-center">
              <p className="text-sm text-gray-400 mb-3">Load wallet intelligence analysis</p>
              <button
                onClick={() => loadTab('wallet_intel')}
                className="rounded-lg bg-violet-600 px-4 py-2 text-xs font-medium text-white hover:bg-violet-500"
              >
                Compute Alpha Scores
              </button>
            </div>
          )}

          {/* Alpha Score IQ Test — 5-bucket backtest */}
          {alphaBacktest && (
            <div className="rounded-xl border border-white/5 bg-surface-raised p-5">
              <div className="mb-4">
                <h3 className="text-sm font-semibold text-gray-300">
                  Alpha Score IQ Test
                  <span className="ml-2 rounded bg-yellow-500/20 px-1.5 py-0.5 text-[10px] text-yellow-400">VALIDATION</span>
                </h3>
                <p className="mt-1 text-xs text-gray-500">
                  5-bucket validation: does the top 20% by copyable_alpha_score actually produce better PnL?
                </p>
              </div>

              {/* Validation verdict */}
              <div className={`mb-4 rounded-lg p-3 text-xs ${
                alphaBacktest.validation.score_predictive
                  ? 'bg-emerald-500/10 border border-emerald-500/20'
                  : 'bg-red-500/10 border border-red-500/20'
              }`}>
                <p className={alphaBacktest.validation.score_predictive ? 'text-emerald-400' : 'text-red-400'}>
                  {alphaBacktest.validation.verdict}
                </p>
                <p className="mt-1 text-gray-500">
                  Top bucket PF: <span className="text-white">{alphaBacktest.validation.top_bucket_pf.toFixed(2)}</span>
                  {' vs '}
                  Bottom bucket PF: <span className="text-white">{alphaBacktest.validation.bottom_bucket_pf.toFixed(2)}</span>
                  {' | '}{alphaBacktest.total_wallets_evaluated} wallets, {alphaBacktest.total_trades_mapped} trades mapped
                </p>
              </div>

              {/* Bucket table */}
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="border-b border-white/5 text-gray-500">
                      <th className="py-2 text-left">Bucket</th>
                      <th className="py-2 text-right">Avg Score</th>
                      <th className="py-2 text-right">Trades</th>
                      <th className="py-2 text-right">PF</th>
                      <th className="py-2 text-right">Win Rate</th>
                      <th className="py-2 text-right">Expectancy</th>
                      <th className="py-2 text-right">Total PnL</th>
                    </tr>
                  </thead>
                  <tbody>
                    {alphaBacktest.buckets.map((b) => {
                      const pfColor = b.profit_factor == null ? 'text-gray-500' : b.profit_factor >= 1.5 ? 'text-emerald-400' : b.profit_factor >= 1.0 ? 'text-yellow-400' : 'text-red-400';
                      return (
                        <tr key={b.bucket} className="border-b border-white/5">
                          <td className="py-2">
                            <span className={`font-medium ${b.bucket === 1 ? 'text-violet-400' : 'text-gray-300'}`}>
                              {b.label}
                            </span>
                            <span className="ml-1 text-[10px] text-gray-600">({b.wallet_count}w)</span>
                          </td>
                          <td className="py-2 text-right tabular-nums text-gray-300">{(b.avg_alpha_score * 100).toFixed(1)}</td>
                          <td className="py-2 text-right tabular-nums text-gray-400">{b.trade_count || '—'}</td>
                          <td className={`py-2 text-right tabular-nums font-bold ${pfColor}`}>
                            {b.profit_factor != null ? b.profit_factor.toFixed(2) : '—'}
                          </td>
                          <td className="py-2 text-right tabular-nums text-gray-300">
                            {b.win_rate != null ? `${(b.win_rate * 100).toFixed(0)}%` : '—'}
                          </td>
                          <td className={`py-2 text-right tabular-nums ${(b.expectancy ?? 0) >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                            {b.expectancy != null ? `${b.expectancy >= 0 ? '+' : ''}${b.expectancy.toFixed(3)}` : '—'}
                          </td>
                          <td className={`py-2 text-right tabular-nums ${b.total_pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                            {b.total_pnl >= 0 ? '+' : ''}{b.total_pnl.toFixed(2)}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <p className="mt-2 text-[10px] text-gray-600">
                Score range shown in parentheses. Ideally PF should decrease from Bucket 1 → 5.
              </p>
            </div>
          )}

          {/* ── Predictive vs Realized Calibration ── */}
          {calibration && (
            <div className="rounded-xl border border-white/5 bg-surface-raised p-5">
              <div className="mb-4 flex items-start justify-between">
                <div>
                  <h3 className="text-sm font-semibold text-gray-300">
                    Predictive vs Realized Calibration
                    <span className={`ml-2 rounded px-1.5 py-0.5 text-[10px] font-bold ${
                      calibration.calibration_verdict === 'well_calibrated'    ? 'bg-emerald-500/20 text-emerald-400' :
                      calibration.calibration_verdict === 'partially_calibrated' ? 'bg-yellow-500/20 text-yellow-400' :
                      'bg-red-500/20 text-red-400'
                    }`}>
                      {calibration.calibration_verdict.replace(/_/g, ' ').toUpperCase()}
                    </span>
                  </h3>
                  <p className="mt-1 text-xs text-gray-500">
                    Does composite score predict actual copy PnL? x = score, bar height = realized PnL.
                  </p>
                </div>
                <div className="text-right shrink-0 ml-4">
                  <p className="text-[10px] text-gray-600">Spearman rank r</p>
                  <p className={`text-2xl font-black tabular-nums ${
                    (calibration.calibration_score ?? 0) > 0.6 ? 'text-emerald-400' :
                    (calibration.calibration_score ?? 0) > 0.2 ? 'text-yellow-400' : 'text-red-400'
                  }`}>
                    {calibration.calibration_score != null ? calibration.calibration_score.toFixed(2) : '—'}
                  </p>
                  <p className="text-[9px] text-gray-600">{calibration.wallets_with_copy_data} wallets w/ data</p>
                </div>
              </div>

              {/* Summary stats */}
              <div className="mb-4 grid grid-cols-3 gap-3 text-xs">
                {[
                  { label: 'Total Wallets', value: calibration.total_wallets },
                  { label: 'Have Copy Data', value: calibration.wallets_with_copy_data },
                  { label: 'Avg Realized PF', value: calibration.avg_realized_pf != null ? calibration.avg_realized_pf.toFixed(2) : '—' },
                ].map(({ label, value }) => (
                  <div key={label} className="rounded border border-white/5 bg-white/[0.02] p-2 text-center">
                    <p className="text-[9px] text-gray-600">{label}</p>
                    <p className="font-bold text-gray-200 tabular-nums">{value}</p>
                  </div>
                ))}
              </div>

              {/* Scatter dot grid: sorted by composite_score, color by realized_pf */}
              <div className="mb-3">
                <p className="text-[10px] text-gray-600 mb-2">Wallets sorted by composite score (left = highest). Color = realized PF quality.</p>
                <div className="flex flex-wrap gap-1.5">
                  {[...calibration.points]
                    .sort((a, b) => b.composite_score - a.composite_score)
                    .map((p) => {
                      const pf = p.realized_pf;
                      const dotColor = !p.has_data ? 'bg-gray-700/60' :
                        pf == null ? 'bg-gray-500' :
                        pf >= 1.5 ? 'bg-emerald-500' :
                        pf >= 1.0 ? 'bg-yellow-500' :
                        'bg-red-500';
                      const size = Math.max(6, Math.min(18, 6 + p.trade_count * 2));
                      return (
                        <div
                          key={p.wallet_id}
                          title={`${p.label}\nscore: ${p.composite_score.toFixed(3)}\nPF: ${pf?.toFixed(2) ?? 'no data'}\ntrades: ${p.trade_count}`}
                          className={`rounded-full cursor-help ${dotColor} transition-opacity hover:opacity-80`}
                          style={{ width: size, height: size }}
                        />
                      );
                    })}
                </div>
                <div className="mt-2 flex items-center gap-4 text-[9px] text-gray-600">
                  <span className="flex items-center gap-1"><span className="inline-block h-2 w-2 rounded-full bg-emerald-500" />PF &ge; 1.5</span>
                  <span className="flex items-center gap-1"><span className="inline-block h-2 w-2 rounded-full bg-yellow-500" />PF 1.0–1.5</span>
                  <span className="flex items-center gap-1"><span className="inline-block h-2 w-2 rounded-full bg-red-500" />PF &lt; 1.0</span>
                  <span className="flex items-center gap-1"><span className="inline-block h-2 w-2 rounded-full bg-gray-700" />No copy data</span>
                  <span className="ml-2">Dot size = # copied trades</span>
                </div>
              </div>

              {/* Table: wallets with copy data */}
              {calibration.wallets_with_copy_data > 0 && (
                <div className="overflow-x-auto">
                  <table className="w-full text-[10px]">
                    <thead>
                      <tr className="border-b border-white/5 text-left text-gray-600">
                        <th className="py-1.5 pr-3">Wallet</th>
                        <th className="py-1.5 pr-3 text-right">Composite</th>
                        <th className="py-1.5 pr-3 text-right">Alpha</th>
                        <th className="py-1.5 pr-3 text-right">Trades</th>
                        <th className="py-1.5 pr-3 text-right">Copy PnL</th>
                        <th className="py-1.5 text-right">Realized PF</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-white/[0.04]">
                      {calibration.points.filter((p) => p.has_data).map((p) => {
                        const pfColor = p.realized_pf == null ? 'text-gray-500' :
                          p.realized_pf >= 1.5 ? 'text-emerald-400' :
                          p.realized_pf >= 1.0 ? 'text-yellow-400' : 'text-red-400';
                        return (
                          <tr key={p.wallet_id} className="hover:bg-white/[0.02]">
                            <td className="py-1.5 pr-3 text-gray-400 truncate max-w-[120px]">{p.label}</td>
                            <td className="py-1.5 pr-3 text-right tabular-nums text-gray-300">{(p.composite_score * 100).toFixed(1)}</td>
                            <td className="py-1.5 pr-3 text-right tabular-nums text-gray-300">{p.copyable_alpha > 0 ? '+' : ''}{p.copyable_alpha.toFixed(4)}</td>
                            <td className="py-1.5 pr-3 text-right tabular-nums text-gray-500">{p.trade_count}</td>
                            <td className={`py-1.5 pr-3 text-right tabular-nums ${p.realized_pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                              {p.realized_pnl >= 0 ? '+' : ''}${p.realized_pnl.toFixed(3)}
                            </td>
                            <td className={`py-1.5 text-right tabular-nums font-bold ${pfColor}`}>
                              {p.realized_pf != null ? p.realized_pf.toFixed(2) : '—'}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
              <p className="mt-2 text-[10px] text-gray-600">{calibration.note}</p>
            </div>
          )}
        </div>
      )}

      {/* ── Leader Impact Tab ─────────────────────────────────────────────── */}
      {tab === 'leader_impact' && (
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="font-semibold text-white">Information Propagation Leaderboard</h2>
              <p className="text-xs text-gray-400 mt-0.5">
                Wallets ranked by measured price impact after their trades. High prop_signal = real information leader.
              </p>
            </div>
            <button
              onClick={() => loadTab('leader_impact')}
              className="rounded bg-white/10 px-3 py-1.5 text-xs text-white hover:bg-white/20 transition"
            >
              Refresh
            </button>
          </div>

          {/* Theory card */}
          <div className="rounded-xl bg-blue-500/5 border border-blue-500/20 p-3 text-xs text-gray-300 space-y-1">
            <p className="font-medium text-blue-400">Information Propagation Theory</p>
            <p>Leader trades → 3-45 second delay → follower wave → price moves.</p>
            <p className="text-gray-500">prop_signal = leader_impact × size_factor × liquidity × time_decay</p>
            <p className="text-gray-500">strong_leader: prop_signal &gt; 0.30 AND hit_rate &gt; 65%</p>
          </div>

          {leaderImpactLoading && (
            <div className="text-center py-10 text-gray-500 text-sm">Computing leader impact...</div>
          )}

          {!leaderImpact && !leaderImpactLoading && (
            <div className="rounded-xl bg-white/5 p-6 text-center space-y-3">
              <p className="text-sm text-gray-400">Load information propagation analysis</p>
              <button
                onClick={() => loadTab('leader_impact')}
                className="rounded bg-blue-500/20 px-4 py-2 text-sm text-blue-300 hover:bg-blue-500/30 transition"
              >
                Load Leader Impact Data
              </button>
            </div>
          )}

          {leaderImpact && (
            <div className="space-y-4">
              {/* Summary stats */}
              <div className="grid grid-cols-3 gap-3">
                <div className="rounded-xl bg-white/5 p-3 text-center">
                  <p className="text-2xl font-bold text-white">{leaderImpact.total}</p>
                  <p className="text-xs text-gray-400 mt-0.5">Wallets analysed</p>
                </div>
                <div className="rounded-xl bg-white/5 p-3 text-center">
                  <p className="text-2xl font-bold text-green-400">{leaderImpact.strong_leaders}</p>
                  <p className="text-xs text-gray-400 mt-0.5">Strong leaders</p>
                </div>
                <div className="rounded-xl bg-white/5 p-3 text-center">
                  <p className="text-2xl font-bold text-yellow-400">
                    {leaderImpact.leaders.filter(w => w.avg_aligned_move_60s != null && w.avg_aligned_move_60s > 0.01).length}
                  </p>
                  <p className="text-xs text-gray-400 mt-0.5">Meaningful move (&gt;1%)</p>
                </div>
              </div>

              {/* Leaderboard table */}
              <div className="rounded-xl bg-white/5 overflow-hidden">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="border-b border-white/10 text-gray-400">
                      <th className="px-3 py-2 text-left">Wallet</th>
                      <th className="px-3 py-2 text-right">Trades</th>
                      <th className="px-3 py-2 text-right">Price Data</th>
                      <th className="px-3 py-2 text-right">Avg Move 60s</th>
                      <th className="px-3 py-2 text-right">Hit Rate</th>
                      <th className="px-3 py-2 text-right">Wallet Score</th>
                      <th className="px-3 py-2 text-right">Prop Signal</th>
                      <th className="px-3 py-2 text-left">Class</th>
                    </tr>
                  </thead>
                  <tbody>
                    {leaderImpact.leaders.map((w, i) => {
                      const classColors: Record<string, string> = {
                        strong_leader: 'text-green-400 bg-green-400/10',
                        moderate_leader: 'text-yellow-400 bg-yellow-400/10',
                        weak_leader: 'text-blue-400 bg-blue-400/10',
                        no_impact: 'text-gray-500 bg-white/5',
                      };
                      const move = w.avg_aligned_move_60s;
                      return (
                        <tr key={w.wallet_id} className="border-b border-white/5 hover:bg-white/5">
                          <td className="px-3 py-2 font-mono text-gray-300">
                            #{i + 1} {w.wallet_id.slice(0, 8)}…
                          </td>
                          <td className="px-3 py-2 text-right text-gray-400">{w.n_trades}</td>
                          <td className="px-3 py-2 text-right text-gray-400">{w.n_with_price_data}</td>
                          <td className={`px-3 py-2 text-right tabular-nums font-medium ${
                            move == null ? 'text-gray-600' : move > 0 ? 'text-green-400' : 'text-red-400'
                          }`}>
                            {move == null ? '—' : `${move > 0 ? '+' : ''}${(move * 100).toFixed(2)}%`}
                          </td>
                          <td className={`px-3 py-2 text-right tabular-nums ${
                            w.hit_rate_60s == null ? 'text-gray-600' :
                            w.hit_rate_60s >= 0.65 ? 'text-green-400' :
                            w.hit_rate_60s >= 0.50 ? 'text-yellow-400' : 'text-red-400'
                          }`}>
                            {w.hit_rate_60s == null ? '—' : `${(w.hit_rate_60s * 100).toFixed(0)}%`}
                          </td>
                          <td className="px-3 py-2 text-right tabular-nums text-gray-300">
                            {w.wallet_score != null ? (w.wallet_score * 100).toFixed(0) : '—'}
                          </td>
                          <td className="px-3 py-2 text-right">
                            <div className="flex items-center justify-end gap-1.5">
                              <div className="h-1.5 w-16 rounded-full bg-white/10">
                                <div
                                  className={`h-full rounded-full transition-all ${
                                    w.prop_signal > 0.3 ? 'bg-green-400' :
                                    w.prop_signal > 0.1 ? 'bg-yellow-400' : 'bg-gray-600'
                                  }`}
                                  style={{ width: `${Math.min(100, w.prop_signal * 200)}%` }}
                                />
                              </div>
                              <span className="tabular-nums text-gray-300 w-10 text-right">
                                {w.prop_signal.toFixed(3)}
                              </span>
                            </div>
                          </td>
                          <td className="px-3 py-2">
                            <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${classColors[w.classification] || 'text-gray-500'}`}>
                              {w.classification.replace('_', ' ')}
                            </span>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>

              <p className="text-[10px] text-gray-600">{leaderImpact.note}</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}