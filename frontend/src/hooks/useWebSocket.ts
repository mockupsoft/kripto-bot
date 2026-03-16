'use client';

import { useEffect, useRef, useCallback, useState } from 'react';
import { WsClient } from '@/lib/ws';

export function useWebSocket() {
  const clientRef = useRef<WsClient | null>(null);
  const [connected, setConnected] = useState(false);
  const [lastMessage, setLastMessage] = useState<{ type: string; data: any } | null>(null);

  useEffect(() => {
    const client = new WsClient();
    clientRef.current = client;

    const unsub = client.subscribe((msg) => {
      setLastMessage(msg);
      setConnected(true);
    });

    client.connect();

    return () => {
      unsub();
      client.disconnect();
    };
  }, []);

  const subscribe = useCallback(
    (handler: (data: { type: string; data: any }) => void) => {
      return clientRef.current?.subscribe(handler) ?? (() => {});
    },
    []
  );

  return { connected, lastMessage, subscribe };
}
