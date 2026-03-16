'use client';

import { useEffect, useState, useCallback, useRef } from 'react';

const API = 'http://localhost:8002';
const PAGE_SIZE = 50;

interface Trade {
  id: string;
  market_id: string;
  market_question: string | null;
  strategy: string;
  side: string;
  outcome: string;
  avg_entry_price: number | null;
  avg_exit_price: number | null;
  total_size: number | null;
  realized_pnl: number | null;
  unrealized_pnl: number | null;
  total_fees: number | null;
  total_slippage: number | null;
  status: 'open' | 'closed';
  exit_reason: string | null;
  opened_at: string | null;
  closed_at: string | null;
}

interface TradesResponse {
  total: number;
  total_open: number;
  total_closed: number;
  limit: number;
  offset: number;
  strategies: string[];
  exit_reasons: string[];
  trades: Trade[];
}

function fmtUsd(v: number | null | undefined) {
  if (v == null) return '-';
  const abs = Math.abs(v).toFixed(4);
  return v >= 0 ? `+$${abs}` : `-$${abs}`;
}

function fmtDate(iso: string | null) {
  if (!iso) return '-';
  return new Date(iso).toLocaleString('tr-TR', {
    timeZone: 'Europe/Istanbul',
    dateStyle: 'short',
    timeStyle: 'medium',
  });
}

function fmtDuration(openedAt: string | null, closedAt: string | null) {
  if (!openedAt) return '-';
  const end = closedAt ? new Date(closedAt) : new Date();
  const ms = end.getTime() - new Date(openedAt).getTime();
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}

const STRATEGY_COLORS: Record<string, string> = {
  direct_copy:     'bg-blue-500/15 text-blue-300',
  high_conviction: 'bg-purple-500/15 text-purple-300',
  leader_copy:     'bg-yellow-500/15 text-yellow-300',
  dislocation:     'bg-orange-500/15 text-orange-300',
  shadow:          'bg-white/5 text-gray-400',
};

export default function TradesPage() {
  const [data, setData] = useState<TradesResponse | null>(null);
  const [page, setPage] = useState(0);
  const [filterStatus, setFilterStatus] = useState('');
  const [filterStrategy, setFilterStrategy] = useState('');
  const [filterExit, setFilterExit] = useState('');
  const [hideZeroPnl, setHideZeroPnl] = useState(false);
  const [loading, setLoading] = useState(false);
  const [liveMode, setLiveMode] = useState(true);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);
  const [newIds, setNewIds] = useState<Set<string>>(new Set());
  const prevIds = useRef<Set<string>>(new Set());
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const buildUrl = useCallback(() => {
    const params = new URLSearchParams({
      limit: String(PAGE_SIZE),
      offset: String(page * PAGE_SIZE),
    });
    if (filterStatus)   params.set('status', filterStatus);
    if (filterStrategy) params.set('strategy', filterStrategy);
    if (filterExit)     params.set('exit_reason', filterExit);
    if (hideZeroPnl)    params.set('hide_zero_pnl', 'true');
    return `${API}/api/trades?${params}`;
  }, [page, filterStatus, filterStrategy, filterExit, hideZeroPnl]);

  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const res = await fetch(buildUrl());
      const json: TradesResponse = await res.json();

      // Detect new trades (for highlight flash)
      const incoming = new Set(json.trades.map(t => t.id));
      const fresh = new Set([...incoming].filter(id => !prevIds.current.has(id)));
      if (fresh.size > 0) {
        setNewIds(fresh);
        setTimeout(() => setNewIds(new Set()), 3000);
      }
      prevIds.current = incoming;

      setData(json);
      setLastRefresh(new Date());
    } catch {
      // silently ignore
    } finally {
      if (!silent) setLoading(false);
    }
  }, [buildUrl]);

  // Initial load
  useEffect(() => { load(); }, [load]);

  // Live polling
  useEffect(() => {
    if (timerRef.current) clearInterval(timerRef.current);
    if (liveMode) {
      timerRef.current = setInterval(() => load(true), 10_000);
    }
    return () => { if (timerRef.current) clearInterval(timerRef.current); };
  }, [liveMode, load]);

  // Reset to page 0 when filters change
  useEffect(() => { setPage(0); }, [filterStatus, filterStrategy, filterExit, hideZeroPnl]);

  const totalPages = data ? Math.ceil(data.total / PAGE_SIZE) : 0;

  const pnl = (t: Trade) => t.status === 'open' ? t.unrealized_pnl : t.realized_pnl;

  // Summary stats — use global counts from API, not current page slice
  const openCount   = data?.total_open   ?? 0;
  const closedCount = data?.total_closed ?? 0;
  const totalPnl    = data?.trades.reduce((s, t) => s + (pnl(t) ?? 0), 0) ?? 0;

  return (
    <div className="space-y-5">

      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-bold">Simulated Trades</h1>
          <p className="text-sm text-gray-500">
            {data ? `${data.total.toLocaleString()} toplam pozisyon` : 'Yükleniyor...'}
            {lastRefresh && (
              <span className="ml-2 text-gray-600">
                · Son güncelleme {lastRefresh.toLocaleTimeString('tr-TR', { timeZone: 'Europe/Istanbul' })}
              </span>
            )}
          </p>
        </div>
        <button
          onClick={() => setLiveMode(v => !v)}
          className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${
            liveMode
              ? 'bg-green-500/15 text-green-400 hover:bg-green-500/25'
              : 'bg-white/5 text-gray-400 hover:bg-white/10'
          }`}
        >
          <span className={`h-1.5 w-1.5 rounded-full ${liveMode ? 'bg-green-400 animate-pulse' : 'bg-gray-500'}`} />
          {liveMode ? 'Live' : 'Paused'}
        </button>
      </div>

      {/* Summary bar */}
      {data && (
        <div className="grid grid-cols-4 gap-3">
          {[
            { label: 'Toplam', value: data.total, color: 'text-white' },
            { label: 'Açık',   value: openCount,  color: 'text-blue-400' },
            { label: 'Kapandı', value: closedCount, color: 'text-gray-300' },
            {
              label: 'Sayfa PnL',
              value: (totalPnl >= 0 ? '+' : '') + '$' + Math.abs(totalPnl).toFixed(4),
              color: totalPnl >= 0 ? 'text-green-400' : 'text-red-400',
            },
          ].map(s => (
            <div key={s.label} className="rounded-xl border border-white/5 bg-surface-raised px-4 py-3">
              <p className="text-xs text-gray-500">{s.label}</p>
              <p className={`text-lg font-bold ${s.color}`}>{s.value}</p>
            </div>
          ))}
        </div>
      )}

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-3 rounded-xl border border-white/5 bg-surface-raised px-4 py-3">
        <span className="text-xs font-medium text-gray-400">Filtrele:</span>

        {/* Status */}
        <select
          value={filterStatus}
          onChange={e => setFilterStatus(e.target.value)}
          className="rounded-lg bg-white/5 px-3 py-1.5 text-xs text-white outline-none hover:bg-white/10"
        >
          <option value="">Tüm durumlar</option>
          <option value="open">Acik</option>
          <option value="closed">Kapali</option>
        </select>

        {/* Strategy */}
        <select
          value={filterStrategy}
          onChange={e => setFilterStrategy(e.target.value)}
          className="rounded-lg bg-white/5 px-3 py-1.5 text-xs text-white outline-none hover:bg-white/10"
        >
          <option value="">Tüm stratejiler</option>
          {(data?.strategies ?? []).map(s => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>

        {/* Exit reason */}
        <select
          value={filterExit}
          onChange={e => setFilterExit(e.target.value)}
          className="rounded-lg bg-white/5 px-3 py-1.5 text-xs text-white outline-none hover:bg-white/10"
        >
          <option value="">Tüm çıkışlar</option>
          {(data?.exit_reasons ?? []).map(r => (
            <option key={r} value={r}>{r}</option>
          ))}
        </select>

        {/* Hide zero PnL */}
        <label className="flex cursor-pointer items-center gap-1.5 text-xs text-gray-400">
          <input
            type="checkbox"
            checked={hideZeroPnl}
            onChange={e => setHideZeroPnl(e.target.checked)}
            className="accent-blue-500"
          />
          PnL=0 gizle
        </label>

        {/* Clear filters */}
        {(filterStatus || filterStrategy || filterExit || hideZeroPnl) && (
          <button
            onClick={() => { setFilterStatus(''); setFilterStrategy(''); setFilterExit(''); setHideZeroPnl(false); }}
            className="rounded-lg bg-red-500/10 px-2 py-1 text-xs text-red-400 hover:bg-red-500/20"
          >
            Temizle
          </button>
        )}

        <div className="ml-auto flex items-center gap-2">
          <button
            onClick={() => load()}
            disabled={loading}
            className="rounded-lg bg-white/5 px-3 py-1.5 text-xs text-gray-300 hover:bg-white/10 disabled:opacity-40"
          >
            {loading ? 'Yükleniyor...' : 'Yenile'}
          </button>
        </div>
      </div>

      {/* Table */}
      <div className="overflow-x-auto rounded-xl border border-white/5">
        <table className="w-full text-sm">
          <thead className="border-b border-white/5 bg-surface-raised text-left text-xs text-gray-500">
            <tr>
              <th className="px-4 py-3">Strateji</th>
              <th className="px-4 py-3">Market</th>
              <th className="px-4 py-3">Taraf</th>
              <th className="px-4 py-3">Giris</th>
              <th className="px-4 py-3">Cikis</th>
              <th className="px-4 py-3">Boyut</th>
              <th className="px-4 py-3">PnL</th>
              <th className="px-4 py-3">Ucret</th>
              <th className="px-4 py-3">Kayma</th>
              <th className="px-4 py-3">Sure</th>
              <th className="px-4 py-3">Durum</th>
              <th className="px-4 py-3">Cikis Nedeni</th>
              <th className="px-4 py-3">Acilis</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-white/5">
            {data?.trades.map(t => {
              const isNew = newIds.has(t.id);
              const p = pnl(t);
              const isOpen = t.status === 'open';
              return (
                <tr
                  key={t.id}
                  className={`transition-colors hover:bg-white/[0.02] ${isNew ? 'bg-blue-500/10' : ''}`}
                >
                  <td className="px-4 py-2.5">
                    <span className={`rounded px-2 py-0.5 text-xs font-medium ${STRATEGY_COLORS[t.strategy] ?? 'bg-white/5 text-gray-400'}`}>
                      {t.strategy}
                    </span>
                  </td>
                  <td className="max-w-[200px] px-4 py-2.5">
                    <span className="block truncate text-xs text-gray-300" title={t.market_question ?? t.market_id}>
                      {t.market_question ? t.market_question.slice(0, 40) + (t.market_question.length > 40 ? '...' : '') : t.market_id.slice(0, 8) + '...'}
                    </span>
                  </td>
                  <td className="px-4 py-2.5 text-xs">
                    <span className={t.side === 'BUY' ? 'text-green-400' : 'text-red-400'}>{t.side}</span>
                    <span className="ml-1 text-gray-500">{t.outcome}</span>
                  </td>
                  <td className="px-4 py-2.5 font-mono text-xs">{t.avg_entry_price?.toFixed(4) ?? '-'}</td>
                  <td className="px-4 py-2.5 font-mono text-xs">{t.avg_exit_price?.toFixed(4) ?? '-'}</td>
                  <td className="px-4 py-2.5 font-mono text-xs">{t.total_size?.toFixed(3) ?? '-'}</td>
                  <td className={`px-4 py-2.5 font-mono text-xs font-medium ${
                    p == null ? 'text-gray-500' : p > 0 ? 'text-green-400' : p < 0 ? 'text-red-400' : 'text-gray-500'
                  }`}>
                    {p == null ? '-' : fmtUsd(p)}
                    {isOpen && p != null && <span className="ml-1 text-gray-600 text-[10px]">unr</span>}
                  </td>
                  <td className="px-4 py-2.5 font-mono text-xs text-gray-500">{fmtUsd(t.total_fees)}</td>
                  <td className="px-4 py-2.5 font-mono text-xs text-gray-500">{fmtUsd(t.total_slippage)}</td>
                  <td className="px-4 py-2.5 text-xs text-gray-400">{fmtDuration(t.opened_at, t.closed_at)}</td>
                  <td className="px-4 py-2.5">
                    <span className={`rounded px-2 py-0.5 text-xs ${
                      isOpen
                        ? 'bg-blue-500/15 text-blue-300'
                        : 'bg-white/5 text-gray-400'
                    }`}>
                      {t.status}
                    </span>
                    {isNew && <span className="ml-1 text-[10px] text-blue-400">YENI</span>}
                  </td>
                  <td className="px-4 py-2.5 text-xs text-gray-500">{t.exit_reason ?? '-'}</td>
                  <td className="px-4 py-2.5 text-xs text-gray-500">{fmtDate(t.opened_at)}</td>
                </tr>
              );
            })}

            {data && data.trades.length === 0 && (
              <tr>
                <td colSpan={13} className="px-4 py-10 text-center text-gray-500">
                  Bu filtre kombinasyonu için işlem bulunamadi.
                </td>
              </tr>
            )}

            {!data && (
              <tr>
                <td colSpan={13} className="px-4 py-10 text-center text-gray-500">
                  Yukluyor...
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between text-sm">
          <span className="text-gray-500">
            {page * PAGE_SIZE + 1}–{Math.min((page + 1) * PAGE_SIZE, data?.total ?? 0)} / {data?.total ?? 0}
          </span>
          <div className="flex items-center gap-1">
            <button
              onClick={() => setPage(0)}
              disabled={page === 0}
              className="rounded px-2 py-1 text-xs text-gray-400 hover:bg-white/5 disabled:opacity-30"
            >
              &laquo;
            </button>
            <button
              onClick={() => setPage(p => Math.max(0, p - 1))}
              disabled={page === 0}
              className="rounded px-3 py-1 text-xs text-gray-400 hover:bg-white/5 disabled:opacity-30"
            >
              Onceki
            </button>

            {/* Page numbers */}
            {Array.from({ length: Math.min(7, totalPages) }, (_, i) => {
              let pg: number;
              if (totalPages <= 7) {
                pg = i;
              } else if (page < 4) {
                pg = i;
              } else if (page > totalPages - 4) {
                pg = totalPages - 7 + i;
              } else {
                pg = page - 3 + i;
              }
              return (
                <button
                  key={pg}
                  onClick={() => setPage(pg)}
                  className={`rounded px-2.5 py-1 text-xs ${
                    pg === page
                      ? 'bg-blue-500/20 text-blue-300 font-medium'
                      : 'text-gray-400 hover:bg-white/5'
                  }`}
                >
                  {pg + 1}
                </button>
              );
            })}

            <button
              onClick={() => setPage(p => Math.min(totalPages - 1, p + 1))}
              disabled={page >= totalPages - 1}
              className="rounded px-3 py-1 text-xs text-gray-400 hover:bg-white/5 disabled:opacity-30"
            >
              Sonraki
            </button>
            <button
              onClick={() => setPage(totalPages - 1)}
              disabled={page >= totalPages - 1}
              className="rounded px-2 py-1 text-xs text-gray-400 hover:bg-white/5 disabled:opacity-30"
            >
              &raquo;
            </button>
          </div>
          <span className="text-gray-500">
            {page + 1} / {totalPages} sayfa
          </span>
        </div>
      )}
    </div>
  );
}
