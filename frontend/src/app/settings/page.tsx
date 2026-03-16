'use client';

import { useEffect, useState } from 'react';
import { apiFetch } from '@/lib/api';

interface SettingsData {
  latency: { detection_delay_ms: number; decision_delay_ms: number; execution_delay_ms: number };
  slippage: { base_slippage_bps: number; volatility_multiplier: number; use_book_walking: boolean };
  risk: {
    starting_balance: number;
    max_position_pct: number;
    max_total_exposure_pct: number;
    max_correlated_positions: number;
    consecutive_loss_cooldown: number;
    kelly_fraction: number;
    daily_loss_stop_pct: number;
  };
}

function SettingRow({ label, value }: { label: string; value: string | number | boolean }) {
  return (
    <div className="flex items-center justify-between py-2 border-b border-white/5">
      <span className="text-sm text-gray-400">{label}</span>
      <span className="text-sm font-medium">{String(value)}</span>
    </div>
  );
}

export default function SettingsPage() {
  const [settings, setSettings] = useState<SettingsData | null>(null);

  useEffect(() => {
    apiFetch<SettingsData>('/api/settings').then(setSettings).catch(() => {});
  }, []);

  if (!settings) return <div className="text-gray-500">Loading...</div>;

  return (
    <div className="space-y-6 max-w-2xl">
      <div>
        <h1 className="text-xl font-bold">Configuration</h1>
        <p className="text-sm text-gray-500">Risk, latency, and slippage profiles</p>
      </div>

      <div className="rounded-xl border border-white/5 bg-surface-raised p-5">
        <h2 className="text-sm font-semibold text-gray-300 mb-3">Latency Profile</h2>
        <SettingRow label="Detection Delay" value={`${settings.latency.detection_delay_ms}ms`} />
        <SettingRow label="Decision Delay" value={`${settings.latency.decision_delay_ms}ms`} />
        <SettingRow label="Execution Delay" value={`${settings.latency.execution_delay_ms}ms`} />
      </div>

      <div className="rounded-xl border border-white/5 bg-surface-raised p-5">
        <h2 className="text-sm font-semibold text-gray-300 mb-3">Slippage Profile</h2>
        <SettingRow label="Base Slippage" value={`${settings.slippage.base_slippage_bps} bps`} />
        <SettingRow label="Volatility Multiplier" value={settings.slippage.volatility_multiplier} />
        <SettingRow label="Book Walking" value={settings.slippage.use_book_walking ? 'Enabled' : 'Disabled'} />
      </div>

      <div className="rounded-xl border border-white/5 bg-surface-raised p-5">
        <h2 className="text-sm font-semibold text-gray-300 mb-3">Risk Profile</h2>
        <SettingRow label="Starting Balance" value={`$${settings.risk.starting_balance}`} />
        <SettingRow label="Max Position" value={`${(settings.risk.max_position_pct * 100).toFixed(0)}%`} />
        <SettingRow label="Max Total Exposure" value={`${(settings.risk.max_total_exposure_pct * 100).toFixed(0)}%`} />
        <SettingRow label="Max Correlated Positions" value={settings.risk.max_correlated_positions} />
        <SettingRow label="Consecutive Loss Cooldown" value={`${settings.risk.consecutive_loss_cooldown} trades`} />
        <SettingRow label="Kelly Fraction" value={settings.risk.kelly_fraction} />
        <SettingRow label="Daily Loss Stop" value={`${(settings.risk.daily_loss_stop_pct * 100).toFixed(0)}%`} />
      </div>
    </div>
  );
}
