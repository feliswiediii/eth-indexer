"""Ethereum JSON-RPC client with retries, failover, and rate limiting."""

import asyncio
import itertools
import logging
import time
from urllib.parse import urlparse

import httpx

from . import metrics

log = logging.getLogger(__name__)


class RpcError(Exception):
    pass


class RateLimiter:
    """Token bucket rate limiter enforcing max_rps."""

    def __init__(self, max_rps: float):
        self.interval = 1.0 / max_rps if max_rps > 0 else 0
        self._next = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self, n: int = 1) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = self._next - now
            self._next = max(now, self._next) + self.interval * n
        if wait > 0:
            await asyncio.sleep(wait)


def _label(url: str) -> str:
    # Extract hostname only; URL path often contains API keys
    return urlparse(url).hostname or "unknown"


class EthRpc:
    def __init__(self, urls: tuple[str, ...], max_rps: float, retries: int = 5):
        if not urls:
            raise RuntimeError("RPC_URLS is empty")
        self.urls = urls
        self.active = 0
        self.retries = retries
        self.limiter = RateLimiter(max_rps)
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(20.0))
        self._ids = itertools.count(1)

    async def close(self) -> None:
        await self.client.aclose()

    def _switch(self) -> None:
        if len(self.urls) > 1:
            self.active = (self.active + 1) % len(self.urls)
            log.warning("switching to RPC #%d (%s)", self.active, _label(self.urls[self.active]))
            metrics.RPC_FAILOVERS.inc()

    async def _post(self, payload, methods: list[str]):
        """Send request (single or batch) with retries and failover."""
        delay = 1.0
        for attempt in range(1, self.retries + 1):
            url = self.urls[self.active]
            endpoint = _label(url)
            await self.limiter.acquire(len(methods))
            started = time.monotonic()
            try:
                resp = await self.client.post(url, json=payload)
                if resp.status_code == 429:
                    raise RpcError("rate limited (429)")
                resp.raise_for_status()
                data = resp.json()
                for m in methods:
                    metrics.RPC_REQUESTS.labels(method=m, endpoint=endpoint, status="ok").inc()
                metrics.RPC_LATENCY.labels(endpoint=endpoint).observe(time.monotonic() - started)
                return data
            except (httpx.HTTPError, RpcError, ValueError) as e:
                for m in methods:
                    metrics.RPC_REQUESTS.labels(method=m, endpoint=endpoint, status="error").inc()
                # Strip URL from error message to avoid leaking API keys
                reason = f"{e.__class__.__name__}: {e}".replace(url, endpoint)
                log.warning("RPC %s failed (attempt %d/%d): %s",
                            endpoint, attempt, self.retries, reason)
                if attempt == self.retries:
                    raise RpcError(f"RPC failed after {self.retries} attempts: {reason}") from None
                self._switch()
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30)

    async def call(self, method: str, params: list):
        data = await self._post(
            {"jsonrpc": "2.0", "id": next(self._ids), "method": method, "params": params},
            [method],
        )
        if "error" in data:
            raise RpcError(f"{method}: {data['error']}")
        return data["result"]

    async def batch(self, calls: list[tuple[str, list]]) -> list:
        """Multiple calls in one HTTP request. Returns results in same order."""
        payload = [
            {"jsonrpc": "2.0", "id": i, "method": m, "params": p} for i, (m, p) in enumerate(calls)
        ]
        data = await self._post(payload, [m for m, _ in calls])
        if not isinstance(data, list):
            raise RpcError(f"batch: unexpected response {str(data)[:200]}")
        by_id = {item.get("id"): item for item in data}
        results = []
        for i, (m, _) in enumerate(calls):
            item = by_id.get(i)
            if item is None or "error" in item:
                raise RpcError(f"{m}: {item.get('error') if item else 'missing response'}")
            results.append(item["result"])
        return results
    async def block_number(self) -> int:
        return int(await self.call("eth_blockNumber", []), 16)

    async def get_blocks(self, numbers: list[int]) -> list[dict]:
        return await self.batch([("eth_getBlockByNumber", [hex(n), True]) for n in numbers])

    async def get_logs(self, from_block: int, to_block: int, addresses: list[str], topic0: str):
        return await self.call(
            "eth_getLogs",
            [{
                "fromBlock": hex(from_block),
                "toBlock": hex(to_block),
                "address": addresses,
                "topics": [topic0],
            }],
        )
