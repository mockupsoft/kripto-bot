'use client';

import { useEffect, useState } from 'react';
import { useParams } from 'next/navigation';
import { apiFetch } from '@/lib/api';
import { fmtPct, fmtNum, fmtUsd, fmtDate } from '@/lib/format';

interface WalletDetail {
  id: string;
  address: string;
  label: string | null;
  is_tracked: boolean;
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
    explanation: Record<string, any>;
  } | null;
}

interface WalletTrade {
  id: string;
  market_id: string;
  side: string;
  outcome: string;
  price: number | null;
  size: number | null;
  occurred_at: string | null;
  detected_at: string | null;
  detection_lag_ms: number | null;
}

function DecayCurveChart({ curve }: { curve: Record<string, number> }) {
  const entries = Object.entries(curve);
  if (entries.length === 0) return <p className="text-gray-500 text-sm">No decay data</p>;

  const maxVal = Math.max(...entries.map(([, v]) => Math.abs(v)), 0.01);

  return (
    <div className="space-y-2">
      <h3 className="text-sm font-semibold text-gray-300">Copy Decay Curve</h3>
      <p className="text-xs text-gray-500">Edge remaining at various copy latencies</p>
      <div className="space-y-1.5">
        {entries.map(([label, value]) => {
          const pct = Math.max(0, (value / maxVal) * 100);
          const color = value > 0.5 ? 'bg-accent-green' : value > 0 ? 'bg-accent-amber' : 'bg-accent-red';
          return (
            <div key={label} className="flex items-center gap-3">
              <span className="w-16 text-xs text-gray-400 text-right">{label}</span>
              <div className="flex-1 h-4 bg-white/5 rounded overflow-hidden">
                <div className={`h-full ${color} rounded transition-all`} style={{ width: `${pct}%` }} />
              </div>
              <span className={`w-14 text-xs text-right ${value > 0 ? 'text-accent-green' : 'text-accent-red'}`}>
                {fmtPct(value)}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default function WalletDetailPage() {
  const params = useParams();
  const [wallet, setWallet] = useState<WalletDetail | null>(null);
  const [trades, setTrades] = useState<WalletTrade[]>([]);

  useEffect(() => {
    if (!params.id) return;
    apiFetch<WalletDetail>(`/api/wallets/${params.id}`).then(setWallet).catch(() => {});
    apiFetch<{ trades: WalletTrade[] }>(`/api/wallets/${params.id}/trades`).then(d => setTrades(d.trades)).catch(() => {});
  }, [params.id]);

  if (!wallet) return <div className="text-gray-500">Loading...</div>;

  const s = wallet.score;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-bold">{wallet.label || 'Wallet Detail'}</h1>
        <p className="text-sm text-gray-500 font-mono">{wallet.address}</p>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <div className="rounded-xl border border-white/5 bg-surface-raised p-5 space-y-3">
          <h2 className="text-sm font-semibold text-gray-300">Score Breakdown</h2>
          {s ? (
            <div className="grid grid-cols-2 gap-2 text-sm">
              <div><span className="text-gray-500">Composite:</span> <span className="font-medium">{fmtNum(s.composite)}</span></div>
              <div><span className="text-gray-500">Copyability:</span> <span className="font-medium">{fmtNum(s.copyability)}</span></div>
              <div><span className="text-gray-500">ROI:</span> <span className="font-medium">{fmtPct(s.total_roi)}</span></div>
              <div><span className="text-gray-500">Hit Rate:</span> <span className="font-medium">{fmtPct(s.hit_rate)}</span></div>
              <div><span className="text-gray-500">Max Drawdown:</span> <span className="font-medium">{fmtPct(s.max_drawdown)}</span></div>
              <div><span className="text-gray-500">Consistency:</span> <span className="font-medium">{fmtNum(s.consistency)}</span></div>
              <div><span className="text-gray-500">Suspiciousness:</span> <span className="font-medium">{fmtNum(s.suspiciousness)}</span></div>
              <div><span className="text-gray-500">Classification:</span> <span className="font-medium">{s.classification}</span></div>
            </div>
          ) : <p className="text-gray-500 text-sm">No scores computed yet</p>}
        </div>

        <div className="rounded-xl border border-white/5 bg-surface-raised p-5">
          {s?.copy_decay_curve ? <DecayCurveChart curve={s.copy_decay_curve} /> : <p className="text-sm text-gray-500">No decay data</p>}
        </div>
      </div>

      <div className="rounded-xl border border-white/5 bg-surface-raised p-5">
        <h2 className="text-sm font-semibold text-gray-300 mb-3">Recent Trades ({trades.length})</h2>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-gray-500 border-b border-white/5">
              <tr>
                <th className="px-3 py-2">Side</th>
                <th className="px-3 py-2">Price</th>
                <th className="px-3 py-2">Size</th>
                <th className="px-3 py-2">Occurred</th>
                <th className="px-3 py-2">Detected</th>
                <th className="px-3 py-2">Lag</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5">
              {trades.slice(0, 20).map(t => (
                <tr key={t.id} className="hover:bg-white/[0.02]">
                  <td className={`px-3 py-2 ${t.side === 'BUY' ? 'text-accent-green' : 'text-accent-red'}`}>{t.side}</td>
                  <td className="px-3 py-2">{t.price?.toFixed(4)}</td>
                  <td className="px-3 py-2">{t.size?.toFixed(2)}</td>
                  <td className="px-3 py-2 text-xs text-gray-400">{fmtDate(t.occurred_at)}</td>
                  <td className="px-3 py-2 text-xs text-gray-400">{fmtDate(t.detected_at)}</td>
                  <td className="px-3 py-2 text-xs">
                    {t.detection_lag_ms != null ? (
                      <span className={t.detection_lag_ms < 1000 ? 'text-accent-green' : t.detection_lag_ms < 5000 ? 'text-accent-amber' : 'text-accent-red'}>
                        {t.detection_lag_ms}ms
                      </span>
                    ) : '-'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
