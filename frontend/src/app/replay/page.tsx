'use client';

import { useState } from 'react';
import { apiFetch } from '@/lib/api';
import { fmtUsd, fmtPct } from '@/lib/format';

interface MCResult {
  n_simulations: number;
  percentiles: Record<string, number>;
  max_drawdown_distribution: Record<string, number>;
  ruin_probability: number;
  median_final_equity: number;
  mean_final_equity: number;
}

interface SensitivityPoint {
  delay_ms?: number;
  multiplier?: number;
  median_equity: number;
  ruin_probability: number;
  p5_equity: number;
}

export default function ReplayPage() {
  const [mcResult, setMcResult] = useState<MCResult | null>(null);
  const [latencySweep, setLatencySweep] = useState<SensitivityPoint[]>([]);
  const [slippageSweep, setSlippageSweep] = useState<SensitivityPoint[]>([]);
  const [loading, setLoading] = useState('');

  async function runMonteCarlo() {
    setLoading('mc');
    try {
      const result = await apiFetch<MCResult>('/api/replay/monte-carlo', {
        method: 'POST',
        body: JSON.stringify({ n_simulations: 600, random_seed: 42 }),
      });
      setMcResult(result);
    } finally {
      setLoading('');
    }
  }

  async function runLatencySweep() {
    setLoading('latency');
    try {
      const data = await apiFetch<{ sweep: SensitivityPoint[] }>('/api/replay/sensitivity/latency');
      setLatencySweep(data.sweep);
    } finally {
      setLoading('');
    }
  }

  async function runSlippageSweep() {
    setLoading('slippage');
    try {
      const data = await apiFetch<{ sweep: SensitivityPoint[] }>('/api/replay/sensitivity/slippage');
      setSlippageSweep(data.sweep);
    } finally {
      setLoading('');
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-bold">Replay Lab</h1>
        <p className="text-sm text-gray-500">Monte Carlo simulation, latency/slippage sensitivity sweeps</p>
      </div>

      {/* Monte Carlo */}
      <div className="rounded-xl border border-white/5 bg-surface-raised p-5">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-sm font-semibold text-gray-300">Monte Carlo Simulation</h2>
          <button
            onClick={runMonteCarlo}
            disabled={loading === 'mc'}
            className="rounded bg-accent-blue px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
          >
            {loading === 'mc' ? 'Running 600 paths...' : 'Run 600 Simulations'}
          </button>
        </div>

        {mcResult && (
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <div>
              <p className="text-xs text-gray-500">Median Final Equity</p>
              <p className="text-lg font-semibold">{fmtUsd(mcResult.median_final_equity)}</p>
            </div>
            <div>
              <p className="text-xs text-gray-500">Mean Final Equity</p>
              <p className="text-lg font-semibold">{fmtUsd(mcResult.mean_final_equity)}</p>
            </div>
            <div>
              <p className="text-xs text-gray-500">Ruin Probability</p>
              <p className="text-lg font-semibold text-accent-red">{fmtPct(mcResult.ruin_probability)}</p>
            </div>
            <div>
              <p className="text-xs text-gray-500">P5 / P95</p>
              <p className="text-lg font-semibold">
                {fmtUsd(mcResult.percentiles.P5)} / {fmtUsd(mcResult.percentiles.P95)}
              </p>
            </div>
          </div>
        )}
      </div>

      {/* Sensitivity Sweeps */}
      <div className="grid gap-4 md:grid-cols-2">
        <div className="rounded-xl border border-white/5 bg-surface-raised p-5">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-sm font-semibold text-gray-300">Latency Sensitivity</h2>
            <button
              onClick={runLatencySweep}
              disabled={loading === 'latency'}
              className="rounded bg-accent-purple px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
            >
              {loading === 'latency' ? 'Computing...' : 'Run Sweep'}
            </button>
          </div>
          {latencySweep.length > 0 && (
            <div className="space-y-1">
              {latencySweep.map((p, i) => (
                <div key={i} className="flex items-center justify-between text-sm py-1 border-b border-white/5">
                  <span className="text-gray-400">{p.delay_ms}ms</span>
                  <span>{fmtUsd(p.median_equity)}</span>
                  <span className={p.ruin_probability > 0.1 ? 'text-accent-red' : 'text-gray-400'}>
                    {fmtPct(p.ruin_probability)} ruin
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="rounded-xl border border-white/5 bg-surface-raised p-5">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-sm font-semibold text-gray-300">Slippage Sensitivity</h2>
            <button
              onClick={runSlippageSweep}
              disabled={loading === 'slippage'}
              className="rounded bg-accent-amber px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
            >
              {loading === 'slippage' ? 'Computing...' : 'Run Sweep'}
            </button>
          </div>
          {slippageSweep.length > 0 && (
            <div className="space-y-1">
              {slippageSweep.map((p, i) => (
                <div key={i} className="flex items-center justify-between text-sm py-1 border-b border-white/5">
                  <span className="text-gray-400">{p.multiplier}x</span>
                  <span>{fmtUsd(p.median_equity)}</span>
                  <span className={p.ruin_probability > 0.1 ? 'text-accent-red' : 'text-gray-400'}>
                    {fmtPct(p.ruin_probability)} ruin
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
