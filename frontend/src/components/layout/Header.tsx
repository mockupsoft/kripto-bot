'use client';

export function Header() {
  return (
    <header className="flex h-14 items-center justify-between border-b border-white/5 bg-surface-raised px-6">
      <div className="flex items-center gap-4">
        <span className="text-sm text-gray-400">Polymarket Arbitrage Simulator</span>
      </div>
      <div className="flex items-center gap-4">
        <span className="flex items-center gap-1.5 text-xs">
          <span className="h-2 w-2 rounded-full bg-accent-green animate-pulse" />
          <span className="text-gray-400">System Online</span>
        </span>
        <span className="rounded bg-red-500/10 px-2 py-0.5 text-[10px] font-semibold text-red-400">
          DEMO MODE
        </span>
      </div>
    </header>
  );
}
