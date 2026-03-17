'use client';

import { useEffect, useState, useCallback, useRef } from 'react';
import { apiFetch } from '@/lib/api';
import { fmtNum, fmtDate } from '@/lib/format';
import type { Signal } from '@/lib/types';

const PAGE_SIZE = 50;
const POLL_INTERVAL_MS = 8000;

const STRATEGY_COLORS: Record<string, string> = {
  direct_copy: 'bg-blue-500/15 text-blue-400',
  high_conviction: 'bg-purple-500/15 text-purple-400',
  leader_copy: 'bg-teal-500/15 text-teal-400',
  dislocation: 'bg-amber-500/15 text-amber-400',
  shadow: 'bg-gray-500/15 text-gray-400',
};

const SOURCE_COLORS: Record<string, string> = {
  wallet_copy: 'text-cyan-400',
  spread_anomaly: 'text-yellow-400',
};

interface SignalResponse {
  total: number;
  limit: number;
  offset: number;
  signals: Signal[];
}

interface AuditDecision {
  id: string;
  decision: string;
  reject_reason: string | null;
  signal_age_ms: number | null;
  price_at_decision: number | null;
  price_drift: number | null;
  spread_at_decision: number | null;
  kelly_fraction: number | null;
  proposed_size: number | null;
  available_bankroll: number | null;
  current_exposure_pct: number | null;
  edge_at_signal: number | null;
  edge_at_decision: number | null;
  edge_erosion_pct: number | null;
  decided_at: string;
}

interface AuditData {
  signal: Signal;
  decisions: AuditDecision[];
}

function EdgeBar({ value }: { value: number | null }) {
  if (value === null) return <span className="text-gray-600">-</span>;
  const pct = Math.min(Math.abs(value) * 200, 100);
  const positive = value > 0;
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-16 rounded-full bg-white/10">
        <div
          className={`h-full rounded-full ${positive ? 'bg-emerald-500' : 'bg-red-500'}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className={`text-xs tabular-nums ${positive ? 'text-emerald-400' : 'text-red-400'}`}>
        {value > 0 ? '+' : ''}{fmtNum(value, 4)}
      </span>
    </div>
  );
}

function AuditModal({ data, onClose }: { data: AuditData; onClose: () => void }) {
  const { signal, decisions } = data;
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="relative max-h-[85vh] w-full max-w-2xl overflow-y-auto rounded-2xl border border-white/10 bg-[#0f1117] p-6 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <button
          onClick={onClose}
          className="absolute right-4 top-4 text-gray-500 hover:text-white"
        >
          ✕
        </button>

        <h2 className="mb-1 text-base font-semibold">Signal Audit</h2>
        <p className="mb-5 text-xs text-gray-500">{fmtDate(signal.created_at)}</p>

        {/* Signal summary */}
        <div className="mb-5 grid grid-cols-3 gap-3">
          {[
            { label: 'Strategy', value: signal.strategy },
            { label: 'Source', value: signal.source_type },
            { label: 'Side', value: signal.side },
            { label: 'Model P', value: fmtNum(signal.model_probability, 4) },
            { label: 'Market P', value: fmtNum(signal.market_price, 4) },
            { label: 'Confidence', value: fmtNum(signal.model_confidence, 2) },
            { label: 'Raw Edge', value: fmtNum(signal.raw_edge, 4) },
            { label: 'Net Edge', value: fmtNum(signal.net_edge, 4) },
            { label: 'Z-Score', value: signal.spread_z_score ? fmtNum(signal.spread_z_score, 2) : '-' },
          ].map(({ label, value }) => (
            <div key={label} className="rounded-lg border border-white/5 bg-white/[0.03] p-3">
              <p className="text-[10px] text-gray-500">{label}</p>
              <p className="mt-0.5 text-sm font-medium">{value}</p>
            </div>
          ))}
        </div>

        {/* Cost breakdown */}
        {signal.costs_breakdown && Object.keys(signal.costs_breakdown).length > 0 && (
          <div className="mb-5">
            <p className="mb-2 text-xs font-medium text-gray-400">Cost Breakdown</p>
            <div className="flex flex-wrap gap-2">
              {Object.entries(signal.costs_breakdown).map(([k, v]) => (
                <span key={k} className="rounded bg-white/5 px-2 py-1 text-xs">
                  <span className="text-gray-500">{k}: </span>
                  <span className="text-amber-400">{typeof v === 'number' ? v.toFixed(4) : v}</span>
                </span>
              ))}
            </div>
          </div>
        )}

        {/* Decisions */}
        <p className="mb-2 text-xs font-medium text-gray-400">Strategy Decisions ({decisions.length})</p>
        {decisions.length === 0 ? (
          <p className="text-xs text-gray-600">No decisions recorded.</p>
        ) : (
          <div className="space-y-2">
            {decisions.map((d) => (
              <div
                key={d.id}
                className={`rounded-lg border p-3 text-xs ${
                  d.decision === 'accept'
                    ? 'border-emerald-500/30 bg-emerald-500/5'
                    : 'border-red-500/20 bg-red-500/5'
                }`}
              >
                <div className="mb-1.5 flex items-center justify-between">
                  <span
                    className={`rounded px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide ${
                      d.decision === 'accept'
                        ? 'bg-emerald-500/20 text-emerald-400'
                        : 'bg-red-500/20 text-red-400'
                    }`}
                  >
                    {d.decision}
                  </span>
                  <span className="text-gray-600">{fmtDate(d.decided_at)}</span>
                </div>
                {d.reject_reason && (
                  <p className="mb-1.5 text-amber-400">⚠ {d.reject_reason}</p>
                )}
                <div className="grid grid-cols-3 gap-x-4 gap-y-0.5 text-gray-400">
                  {d.signal_age_ms != null && <span>Age: {d.signal_age_ms}ms</span>}
                  {d.price_drift != null && <span>Drift: {d.price_drift > 0 ? '+' : ''}{d.price_drift.toFixed(4)}</span>}
                  {d.spread_at_decision != null && <span>Spread: {d.spread_at_decision.toFixed(4)}</span>}
                  {d.edge_at_signal != null && <span>Edge@signal: {d.edge_at_signal.toFixed(4)}</span>}
                  {d.edge_at_decision != null && <span>Edge@dec: {d.edge_at_decision.toFixed(4)}</span>}
                  {d.edge_erosion_pct != null && (
                    <span className={d.edge_erosion_pct > 0.5 ? 'text-red-400' : ''}>
                      Erosion: {(d.edge_erosion_pct * 100).toFixed(1)}%
                    </span>
                  )}
                  {d.kelly_fraction != null && <span>Kelly: {(d.kelly_fraction * 100).toFixed(1)}%</span>}
                  {d.proposed_size != null && <span>Size: ${d.proposed_size.toFixed(2)}</span>}
                  {d.current_exposure_pct != null && <span>Exposure: {(d.current_exposure_pct * 100).toFixed(1)}%</span>}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export default function SignalsPage() {
  const [signals, setSignals] = useState<Signal[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [filterStrategy, setFilterStrategy] = useState<string>('');
  const [filterSource, setFilterSource] = useState<string>('');
  const [loading, setLoading] = useState(false);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);
  const [newCount, setNewCount] = useState(0);
  const [auditData, setAuditData] = useState<AuditData | null>(null);
  const [auditLoading, setAuditLoading] = useState<string | null>(null);
  const prevTotalRef = useRef(0);

  const fetchSignals = useCallback(
    async (resetPage = false) => {
      setLoading(true);
      const currentPage = resetPage ? 0 : page;
      const params = new URLSearchParams({
        limit: String(PAGE_SIZE),
        offset: String(currentPage * PAGE_SIZE),
      });
      if (filterStrategy) params.set('strategy', filterStrategy);
      if (filterSource) params.set('source_type', filterSource);

      try {
        const data = await apiFetch<SignalResponse>(`/api/signals?${params}`);
        setSignals(data.signals);
        setTotal(data.total);
        setLastRefresh(new Date());

        if (prevTotalRef.current && data.total > prevTotalRef.current && currentPage === 0) {
          setNewCount(data.total - prevTotalRef.current);
          setTimeout(() => setNewCount(0), 3000);
        }
        prevTotalRef.current = data.total;

        if (resetPage) setPage(0);
      } catch {
        // ignore
      } finally {
        setLoading(false);
      }
    },
    [page, filterStrategy, filterSource],
  );

  // Initial + filter change
  useEffect(() => {
    fetchSignals(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filterStrategy, filterSource]);

  // Page change
  useEffect(() => {
    fetchSignals(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page]);

  // Live polling — only first page
  useEffect(() => {
    if (page !== 0) return;
    const id = setInterval(() => fetchSignals(false), POLL_INTERVAL_MS);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, filterStrategy, filterSource]);

  const openAudit = async (signalId: string) => {
    setAuditLoading(signalId);
    try {
      const data = await apiFetch<AuditData>(`/api/signals/${signalId}/audit`);
      setAuditData(data);
    } catch {
      // ignore
    } finally {
      setAuditLoading(null);
    }
  };

  const totalPages = Math.ceil(total / PAGE_SIZE);
  const strategies = ['direct_copy', 'high_conviction', 'leader_copy', 'dislocation', 'shadow'];
  const sourceTypes = ['wallet_copy', 'spread_anomaly'];

  return (
    <div className="space-y-5">
      {auditData && <AuditModal data={auditData} onClose={() => setAuditData(null)} />}

      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-xl font-bold">Signal Feed</h1>
          <p className="text-sm text-gray-500">Every detected opportunity with decision audit</p>
        </div>
        <div className="flex items-center gap-3">
          {newCount > 0 && (
            <span className="animate-pulse rounded-full bg-emerald-500/20 px-3 py-1 text-xs font-medium text-emerald-400">
              +{newCount} new
            </span>
          )}
          {lastRefresh && (
            <span className="text-xs text-gray-600">
              {loading ? (
                <span className="inline-flex items-center gap-1">
                  <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-blue-400" />
                  refreshing…
                </span>
              ) : (
                <>updated {lastRefresh.toLocaleTimeString('tr-TR', { timeZone: 'Europe/Istanbul' })}</>
              )}
            </span>
          )}
          <button
            onClick={() => fetchSignals(false)}
            disabled={loading}
            className="rounded-lg border border-white/10 bg-white/5 px-3 py-1.5 text-xs hover:bg-white/10 disabled:opacity-40"
          >
            ↺ Refresh
          </button>
        </div>
      </div>

      {/* Filters + stats bar */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-1.5 rounded-lg border border-white/10 bg-surface-raised px-1 py-1">
          <button
            onClick={() => setFilterStrategy('')}
            className={`rounded px-3 py-1 text-xs transition-colors ${!filterStrategy ? 'bg-white/10 text-white' : 'text-gray-500 hover:text-gray-300'}`}
          >
            All
          </button>
          {strategies.map((s) => (
            <button
              key={s}
              onClick={() => setFilterStrategy(filterStrategy === s ? '' : s)}
              className={`rounded px-3 py-1 text-xs transition-colors ${filterStrategy === s ? (STRATEGY_COLORS[s] ?? 'bg-white/10 text-white') : 'text-gray-500 hover:text-gray-300'}`}
            >
              {s.replace('_', ' ')}
            </button>
          ))}
        </div>

        <div className="flex items-center gap-1.5 rounded-lg border border-white/10 bg-surface-raised px-1 py-1">
          <button
            onClick={() => setFilterSource('')}
            className={`rounded px-3 py-1 text-xs transition-colors ${!filterSource ? 'bg-white/10 text-white' : 'text-gray-500 hover:text-gray-300'}`}
          >
            All Sources
          </button>
          {sourceTypes.map((s) => (
            <button
              key={s}
              onClick={() => setFilterSource(filterSource === s ? '' : s)}
              className={`rounded px-3 py-1 text-xs transition-colors ${filterSource === s ? 'bg-white/10 text-white' : 'text-gray-500 hover:text-gray-300'}`}
            >
              {s.replace('_', ' ')}
            </button>
          ))}
        </div>

        <span className="ml-auto text-xs text-gray-500">
          {total.toLocaleString()} signals total
        </span>
      </div>

      {/* Table */}
      <div className="overflow-x-auto rounded-xl border border-white/5">
        <table className="w-full text-sm">
          <thead className="border-b border-white/5 bg-surface-raised text-left text-xs text-gray-500">
            <tr>
              <th className="px-4 py-3">Time</th>
              <th className="px-4 py-3">Strategy</th>
              <th className="px-4 py-3">Source</th>
              <th className="px-4 py-3">Side</th>
              <th className="px-4 py-3">Model P</th>
              <th className="px-4 py-3">Market P</th>
              <th className="px-4 py-3">Net Edge</th>
              <th className="px-4 py-3">Z-Score</th>
              <th className="px-4 py-3">Confidence</th>
              <th className="px-4 py-3 text-right">Audit</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-white/5">
            {signals.map((s, i) => (
              <tr
                key={s.id}
                className={`hover:bg-white/[0.025] transition-colors ${i === 0 && page === 0 && newCount > 0 ? 'bg-emerald-500/5' : ''}`}
              >
                <td className="px-4 py-3 text-xs text-gray-400 tabular-nums">{fmtDate(s.created_at)}</td>
                <td className="px-4 py-3">
                  <span className={`rounded px-2 py-0.5 text-[11px] font-medium ${STRATEGY_COLORS[s.strategy] ?? 'bg-white/5 text-gray-300'}`}>
                    {s.strategy.replace('_', ' ')}
                  </span>
                </td>
                <td className={`px-4 py-3 text-xs ${SOURCE_COLORS[s.source_type] ?? 'text-gray-400'}`}>
                  {s.source_type.replace('_', ' ')}
                </td>
                <td className="px-4 py-3 text-xs font-medium">
                  <span className={s.side === 'BUY' ? 'text-emerald-400' : 'text-red-400'}>
                    {s.side}
                  </span>
                </td>
                <td className="px-4 py-3 tabular-nums text-gray-300">{fmtNum(s.model_probability, 4)}</td>
                <td className="px-4 py-3 tabular-nums text-gray-300">{fmtNum(s.market_price, 4)}</td>
                <td className="px-4 py-3">
                  <EdgeBar value={s.net_edge} />
                </td>
                <td className="px-4 py-3 tabular-nums text-gray-400">
                  {s.spread_z_score != null ? (
                    <span className={Math.abs(s.spread_z_score) > 3 ? 'text-amber-400 font-medium' : ''}>
                      {fmtNum(s.spread_z_score, 2)}
                    </span>
                  ) : '-'}
                </td>
                <td className="px-4 py-3 tabular-nums text-gray-400">{fmtNum(s.model_confidence, 2)}</td>
                <td className="px-4 py-3 text-right">
                  <button
                    onClick={() => openAudit(s.id)}
                    disabled={auditLoading === s.id}
                    className="rounded border border-white/10 px-2 py-1 text-[11px] text-gray-400 hover:border-white/20 hover:text-white disabled:opacity-40"
                  >
                    {auditLoading === s.id ? '…' : 'audit'}
                  </button>
                </td>
              </tr>
            ))}
            {signals.length === 0 && !loading && (
              <tr>
                <td colSpan={10} className="px-4 py-12 text-center text-gray-500">
                  {filterStrategy || filterSource
                    ? 'No signals match the current filters.'
                    : 'No signals yet — waiting for the next ingestion cycle.'}
                </td>
              </tr>
            )}
            {loading && signals.length === 0 && (
              <tr>
                <td colSpan={10} className="px-4 py-12 text-center text-gray-600">
                  Loading…
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between">
          <span className="text-xs text-gray-500">
            Showing {page * PAGE_SIZE + 1}–{Math.min((page + 1) * PAGE_SIZE, total)} of {total.toLocaleString()}
          </span>
          <div className="flex items-center gap-1">
            <button
              onClick={() => setPage(0)}
              disabled={page === 0}
              className="rounded border border-white/10 px-2.5 py-1.5 text-xs text-gray-400 hover:text-white disabled:opacity-30"
            >
              «
            </button>
            <button
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              disabled={page === 0}
              className="rounded border border-white/10 px-2.5 py-1.5 text-xs text-gray-400 hover:text-white disabled:opacity-30"
            >
              ‹
            </button>

            {/* Page window */}
            {Array.from({ length: Math.min(7, totalPages) }, (_, i) => {
              const start = Math.max(0, Math.min(page - 3, totalPages - 7));
              const p = start + i;
              return (
                <button
                  key={p}
                  onClick={() => setPage(p)}
                  className={`min-w-[32px] rounded border px-2.5 py-1.5 text-xs transition-colors ${
                    p === page
                      ? 'border-blue-500/50 bg-blue-500/10 text-blue-400'
                      : 'border-white/10 text-gray-500 hover:text-white'
                  }`}
                >
                  {p + 1}
                </button>
              );
            })}

            <button
              onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
              disabled={page >= totalPages - 1}
              className="rounded border border-white/10 px-2.5 py-1.5 text-xs text-gray-400 hover:text-white disabled:opacity-30"
            >
              ›
            </button>
            <button
              onClick={() => setPage(totalPages - 1)}
              disabled={page >= totalPages - 1}
              className="rounded border border-white/10 px-2.5 py-1.5 text-xs text-gray-400 hover:text-white disabled:opacity-30"
            >
              »
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
