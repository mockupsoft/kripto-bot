"""Rate-limit aware HTTP client wrapper for Polymarket endpoints."""

from __future__ import annotations

import asyncio
import time

import httpx


class RateLimitedClient:
    """Wraps httpx.AsyncClient with per-host rate limiting and backoff."""

    def __init__(self, requests_per_second: float = 5.0, max_retries: int = 3):
        self._rps = requests_per_second
        self._min_interval = 1.0 / requests_per_second
        self._last_request_time = 0.0
        self._max_retries = max_retries
        self._client = httpx.AsyncClient(timeout=30.0, follow_redirects=True)
        self._lock = asyncio.Lock()

    async def get(self, url: str, **kwargs) -> httpx.Response:
        return await self._request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs) -> httpx.Response:
        return await self._request("POST", url, **kwargs)

    async def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        for attempt in range(self._max_retries):
            async with self._lock:
                elapsed = time.monotonic() - self._last_request_time
                if elapsed < self._min_interval:
                    await asyncio.sleep(self._min_interval - elapsed)
                self._last_request_time = time.monotonic()

            resp = await self._client.request(method, url, **kwargs)

            if resp.status_code == 429:
                retry_after = float(resp.headers.get("Retry-After", 2 ** attempt))
                await asyncio.sleep(retry_after)
                continue

            resp.raise_for_status()
            return resp

        raise httpx.HTTPStatusError(
            f"Rate limited after {self._max_retries} retries",
            request=httpx.Request(method, url),
            response=resp,
        )

    async def close(self) -> None:
        await self._client.aclose()
