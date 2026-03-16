// All API calls go through Next.js rewrite proxy at /api/*
// This avoids localhost IPv6 resolution issues in the browser.
// Next.js server-side forwards to the actual backend (127.0.0.1:8002).

export async function apiFetch<T = any>(path: string, init?: RequestInit): Promise<T> {
  // path already starts with /api/... — use relative URL so browser hits Next.js proxy
  const url = path.startsWith('/api') ? path : `/api${path}`;
  const res = await fetch(url, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...init?.headers,
    },
  });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}
