"""Ethereum indexer: reads blocks and stores compact summaries in Postgres.

Main loop:
  1. Get chain head block number
  2. Process blocks up to head - CONFIRMATIONS in batches of BATCH_SIZE
  3. For each batch: fetch blocks (batch request) + USDT/USDC Transfer events (eth_getLogs)
  4. Write to DB in one transaction
  5. If caught up, sleep POLL_INTERVAL
"""

import asyncio
import logging
import signal
import threading
import time
from decimal import Decimal
from wsgiref.simple_server import WSGIRequestHandler, make_server

import psycopg
from prometheus_client import make_wsgi_app

from . import db, metrics
from .config import Config
from .rpc import EthRpc

log = logging.getLogger(__name__)

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
STABLES = {
    "0xdac17f958d2ee523a2206206994597c13d831ec7": ("USDT", 6),
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": ("USDC", 6),
}
WEI = Decimal(10) ** 18
GWEI = Decimal(10) ** 9
REORG_REWIND = 10


def _addr_from_topic(topic: str) -> str:
    return "0x" + topic[-40:]


def parse_block(block: dict) -> dict:
    base_fee = block.get("baseFeePerGas")
    return {
        "number": int(block["number"], 16),
        "hash": block["hash"],
        "parent_hash": block["parentHash"],
        "ts": int(block["timestamp"], 16),
        "tx_count": len(block["transactions"]),
        "gas_used": int(block["gasUsed"], 16),
        "gas_limit": int(block["gasLimit"], 16),
        "base_fee_gwei": (Decimal(int(base_fee, 16)) / GWEI) if base_fee else None,
    }


def eth_whales(block: dict, threshold_eth: Decimal) -> list[dict]:
    """Extract large ETH transfers from block transactions."""
    ts = int(block["timestamp"], 16)
    number = int(block["number"], 16)
    out = []
    for tx in block["transactions"]:
        value = Decimal(int(tx["value"], 16)) / WEI
        if value >= threshold_eth:
            out.append({
                "block_number": number, "ts": ts, "tx_hash": tx["hash"], "log_index": -1,
                "token": "ETH", "from_addr": tx["from"], "to_addr": tx.get("to"),
                "amount": value,
            })
    return out


def stable_whales(logs: list[dict], ts_by_block: dict[int, int], threshold: Decimal) -> list[dict]:
    """Extract large stablecoin transfers from Transfer events."""
    out = []
    for lg in logs:
        if lg.get("removed"):
            continue
        token = STABLES.get(lg["address"].lower())
        topics = lg["topics"]
        if token is None or len(topics) < 3 or lg["data"] in ("0x", ""):
            continue
        symbol, decimals = token
        amount = Decimal(int(lg["data"], 16)) / (Decimal(10) ** decimals)
        if amount < threshold:
            continue
        number = int(lg["blockNumber"], 16)
        out.append({
            "block_number": number, "ts": ts_by_block[number], "tx_hash": lg["transactionHash"],
            "log_index": int(lg["logIndex"], 16), "token": symbol,
            "from_addr": _addr_from_topic(topics[1]), "to_addr": _addr_from_topic(topics[2]),
            "amount": amount,
        })
    return out


class Health:
    """Liveness tracker for /healthz probe."""

    def __init__(self, max_silence: float):
        self.max_silence = max_silence
        self.last_tick = time.monotonic()

    def tick(self) -> None:
        self.last_tick = time.monotonic()

    def alive(self) -> bool:
        return time.monotonic() - self.last_tick < self.max_silence


def start_http(port: int, health: Health) -> None:
    """Start /metrics and /healthz server in background thread."""
    metrics_app = make_wsgi_app()

    def app(environ, start_response):
        path = environ.get("PATH_INFO", "")
        if path == "/healthz":
            ok = health.alive()
            start_response("200 OK" if ok else "503 Service Unavailable",
                           [("Content-Type", "text/plain")])
            return [b"ok" if ok else b"stalled"]
        return metrics_app(environ, start_response)

    class QuietHandler(WSGIRequestHandler):
        def log_message(self, *args):
            pass

    server = make_server("0.0.0.0", port, app, handler_class=QuietHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log.info("metrics and health on :%d", port)


class Indexer:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.rpc = EthRpc(cfg.rpc_urls, cfg.max_rps)
        self.stop = asyncio.Event()
        self.health = Health(max_silence=max(300.0, cfg.poll_interval * 20))
        self.whale_eth = Decimal(str(cfg.whale_eth))
        self.whale_stable = Decimal(str(cfg.whale_stable_usd))

    async def run(self) -> None:
        async with await psycopg.AsyncConnection.connect(self.cfg.database_url) as conn:
            await conn.execute(db.SCHEMA)
            await conn.commit()
            log.info("schema ready, starting loop")
            while not self.stop.is_set():
                self.health.tick()
                try:
                    caught_up = await self.step(conn)
                except Exception:
                    metrics.ERRORS.inc()
                    log.exception("loop error, retrying")
                    await conn.rollback()
                    caught_up = True  # подождать перед следующей попыткой
                if caught_up:
                    try:
                        await asyncio.wait_for(self.stop.wait(), timeout=self.cfg.poll_interval)
                    except asyncio.TimeoutError:
                        pass
        await self.rpc.close()
        log.info("stopped")

    async def step(self, conn) -> bool:
        """Process one batch. Returns True if caught up with chain."""
        head = await self.rpc.block_number()
        metrics.CHAIN_HEAD.set(head)
        target = head - self.cfg.confirmations

        row = await (await conn.execute(db.LAST_BLOCK)).fetchone()
        if row is None:
            last, last_hash = target - self.cfg.start_blocks_back, None
            log.info("empty database, starting from block %d", last + 1)
        else:
            last, last_hash = row

        metrics.LAST_INDEXED.set(max(last, 0))
        metrics.LAG.set(max(head - last, 0))
        if last >= target:
            return True

        start = last + 1
        end = min(last + self.cfg.batch_size, target)
        numbers = list(range(start, end + 1))

        with metrics.BATCH_SECONDS.time():
            blocks = await self.rpc.get_blocks(numbers)
            if any(b is None for b in blocks):
                log.warning("RPC returned empty block in %d..%d, will retry", start, end)
                return True

            # Verify chain continuity: each block must reference the previous
            prev_hash = last_hash
            for b in blocks:
                if prev_hash is not None and b["parentHash"] != prev_hash:
                    await self.handle_reorg(conn, int(b["number"], 16))
                    return False
                prev_hash = b["hash"]

            logs = await self.rpc.get_logs(start, end, list(STABLES), TRANSFER_TOPIC)

            parsed = [parse_block(b) for b in blocks]
            ts_by_block = {p["number"]: p["ts"] for p in parsed}
            transfers = [t for b in blocks for t in eth_whales(b, self.whale_eth)]
            transfers += stable_whales(logs, ts_by_block, self.whale_stable)

            async with conn.transaction():
                async with conn.cursor() as cur:
                    await cur.executemany(db.INSERT_BLOCK, parsed)
                    if transfers:
                        await cur.executemany(db.INSERT_TRANSFER, transfers)
            await conn.commit()

        metrics.BLOCKS.inc(len(parsed))
        for t in transfers:
            metrics.TRANSFERS.labels(token=t["token"]).inc()
            log.info("whale %s %s %s -> %s (block %d)", t["amount"], t["token"],
                     t["from_addr"], t["to_addr"], t["block_number"])
        metrics.LAST_INDEXED.set(end)
        metrics.LAG.set(max(head - end, 0))
        log.info("indexed %d..%d (head %d, lag %d)", start, end, head, head - end)
        return end >= target

    async def handle_reorg(self, conn, at_block: int) -> None:
        rewind_to = at_block - REORG_REWIND
        log.warning("reorg detected at block %d, rewinding to %d", at_block, rewind_to)
        metrics.REORGS.inc()
        async with conn.transaction():
            for q in db.DELETE_FROM_BLOCK:
                await conn.execute(q, (rewind_to,))
        await conn.commit()


def main(cfg: Config) -> None:
    indexer = Indexer(cfg)
    start_http(cfg.indexer_http_port, indexer.health)

    async def runner():
        loop = asyncio.get_running_loop()
        # Graceful shutdown on SIGTERM/SIGINT
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, indexer.stop.set)
        await indexer.run()

    asyncio.run(runner())
