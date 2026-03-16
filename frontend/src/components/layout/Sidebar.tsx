'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import clsx from 'clsx';

const NAV_ITEMS = [
  { href: '/', label: 'Overview', icon: '◎' },
  { href: '/wallets', label: 'Wallets', icon: '◈' },
  { href: '/markets', label: 'Markets', icon: '◇' },
  { href: '/trades', label: 'Trades', icon: '⊞' },
  { href: '/signals', label: 'Signals', icon: '⚡' },
  { href: '/analytics', label: 'Analytics', icon: '◰' },
  { href: '/research', label: 'Research Lab', icon: '🔬' },
  { href: '/replay', label: 'Replay Lab', icon: '↻' },
  { href: '/settings', label: 'Settings', icon: '⚙' },
];

export function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="flex w-56 flex-col border-r border-white/5 bg-surface-raised">
      <div className="flex h-14 items-center px-5">
        <span className="text-lg font-bold tracking-tight">
          Poly<span className="text-accent-green">Bot</span>
        </span>
        <span className="ml-2 rounded bg-accent-amber/20 px-1.5 py-0.5 text-[10px] font-medium text-accent-amber">
          DEMO
        </span>
      </div>
      <nav className="mt-2 flex flex-1 flex-col gap-0.5 px-2">
        {NAV_ITEMS.map((item) => (
          <Link
            key={item.href}
            href={item.href}
            className={clsx(
              'flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors',
              pathname === item.href
                ? 'bg-white/10 text-white'
                : 'text-gray-400 hover:bg-white/5 hover:text-gray-200'
            )}
          >
            <span className="text-base">{item.icon}</span>
            {item.label}
          </Link>
        ))}
      </nav>
      <div className="border-t border-white/5 px-4 py-3">
        <p className="text-[10px] text-gray-500">Paper Trading Only</p>
        <p className="text-[10px] text-gray-500">No Real Orders</p>
      </div>
    </aside>
  );
}
