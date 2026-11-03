"""Read-only HTTP API with predefined queries."""

from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from prometheus_client import make_asgi_app
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from .config import Config

cfg = Config()
pool = AsyncConnectionPool(cfg.database_url, min_size=1, max_size=5, open=False,
                           kwargs={"row_factory": dict_row})


@asynccontextmanager
async def lifespan(_: FastAPI):
    await pool.open()
    yield
    await pool.close()


app = FastAPI(title="eth-indexer API", lifespan=lifespan)
app.mount("/metrics", make_asgi_app())


async def fetch(query: str, params: tuple = ()) -> list[dict]:
    async with pool.connection() as conn:
        return await (await conn.execute(query, params)).fetchall()


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.get("/readyz")
async def readyz():
    try:
        await fetch("SELECT 1")
    except Exception as e:
        raise HTTPException(503, f"database unavailable: {e.__class__.__name__}")
    return {"status": "ready"}


@app.get("/gas")
async def gas():
    """Current base fee and last hour statistics."""
    latest = await fetch(
        "SELECT number, ts, base_fee_gwei FROM blocks ORDER BY number DESC LIMIT 1")
    if not latest:
        raise HTTPException(503, "no data yet")
    stats = (await fetch(
        """SELECT avg(base_fee_gwei) AS avg, min(base_fee_gwei) AS min,
                  max(base_fee_gwei) AS max, count(*) AS blocks
           FROM blocks WHERE ts > (SELECT max(ts) FROM blocks) - interval '1 hour'"""))[0]
    return {
        "block": latest[0]["number"],
        "time": latest[0]["ts"],
        "base_fee_gwei": float(latest[0]["base_fee_gwei"] or 0),
        "last_hour": {k: float(v) if v is not None else None for k, v in stats.items()},
    }


@app.get("/blocks/latest")
async def latest_blocks(limit: int = Query(10, ge=1, le=100)):
    return await fetch(
        """SELECT number, hash, ts, tx_count, gas_used, gas_limit,
                  round(gas_used::numeric / gas_limit * 100, 1) AS fullness_pct, base_fee_gwei
           FROM blocks ORDER BY number DESC LIMIT %s""", (limit,))


@app.get("/whales")
async def whales(
    limit: int = Query(20, ge=1, le=200),
    token: Literal["ETH", "USDT", "USDC"] | None = None,
):
    if token:
        return await fetch(
            """SELECT block_number, ts, tx_hash, token, from_addr, to_addr, amount
               FROM transfers WHERE token = %s ORDER BY ts DESC, id DESC LIMIT %s""",
            (token, limit))
    return await fetch(
        """SELECT block_number, ts, tx_hash, token, from_addr, to_addr, amount
           FROM transfers ORDER BY ts DESC, id DESC LIMIT %s""", (limit,))


@app.get("/status")
async def status():
    row = (await fetch(
        """SELECT min(number) AS first_block, max(number) AS last_block,
                  count(*) AS blocks, (SELECT count(*) FROM transfers) AS transfers
           FROM blocks"""))[0]
    return row
