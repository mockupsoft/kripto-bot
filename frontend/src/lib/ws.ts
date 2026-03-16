// WebSocket connects directly to backend.
// Use 127.0.0.1 (explicit IPv4) to avoid Windows localhost→IPv6 resolution.
const WS_URL =
  typeof window !== 'undefined'
    ? `ws://127.0.0.1:8002/ws/live`
    : 'ws://127.0.0.1:8002/ws/live';

type MessageHandler = (data: { type: string; data: any }) => void;

export class WsClient {
  private ws: WebSocket | null = null;
  private handlers: MessageHandler[] = [];
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;

  connect() {
    this.ws = new WebSocket(WS_URL);
    this.ws.onmessage = (event) => {
      try {
        const parsed = JSON.parse(event.data);
        this.handlers.forEach((h) => h(parsed));
      } catch {}
    };
    this.ws.onclose = () => {
      this.reconnectTimer = setTimeout(() => this.connect(), 3000);
    };
    this.ws.onerror = () => this.ws?.close();

    const pingInterval = setInterval(() => {
      if (this.ws?.readyState === WebSocket.OPEN) {
        this.ws.send('ping');
      } else {
        clearInterval(pingInterval);
      }
    }, 10000);
  }

  subscribe(handler: MessageHandler) {
    this.handlers.push(handler);
    return () => {
      this.handlers = this.handlers.filter((h) => h !== handler);
    };
  }

  disconnect() {
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.ws?.close();
  }
}
