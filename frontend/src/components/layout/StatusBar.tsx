'use client';

import { useWebSocket } from '@/hooks/useWebSocket';

export function StatusBar() {
  const { connected } = useWebSocket();

  return (
    <div className="flex items-center gap-2 text-xs text-gray-500">
      <span className={`h-1.5 w-1.5 rounded-full ${connected ? 'bg-accent-green' : 'bg-gray-500'}`} />
      {connected ? 'WS Connected' : 'WS Disconnected'}
    </div>
  );
}
