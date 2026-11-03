# eth-indexer

Small Ethereum indexer for a Kubernetes homelab. It follows mainnet blocks through a public RPC
and stores a compact summary in Postgres:

- **block stats**: tx count, gas used, block fullness, base fee (gas tracker)
- **whale transfers**: ETH transfers ≥ `WHALE_ETH` and USDT/USDC `Transfer` events ≥ `WHALE_STABLE_USD`

```
RPC ──► indexer ──► Postgres ◄── API ◄── client
```

One image, two modes: `python -m ethidx indexer` and `python -m ethidx api`.

## How the indexer works

1. Ask the RPC for the chain head.
2. Process blocks up to `head - CONFIRMATIONS` in batches of `BATCH_SIZE`:
   blocks with transactions in one JSON-RPC batch, stablecoin events in one `eth_getLogs`.
3. Each batch is written in one DB transaction.
4. **Catch-up**: on start it continues from the last block in the DB. An empty DB starts
   `START_BLOCKS_BACK` blocks behind the head, not from genesis.
5. **Reorg protection**: blocks are processed with a `CONFIRMATIONS` delay, and every block's
   `parentHash` is checked against the stored previous block. On mismatch the last 10 blocks are
   deleted and re-indexed.
6. **RPC failures**: retries with exponential backoff; on error switches to the next URL in `RPC_URLS`.
   Requests are rate-limited to `MAX_RPS`.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | — | Postgres URI (CloudNativePG secret `<cluster>-app`, key `uri`) |
| `RPC_URLS` | — | comma-separated RPC URLs, first is primary |
| `CONFIRMATIONS` | 5 | blocks to wait before indexing |
| `POLL_INTERVAL` | 6 | seconds between head checks when caught up |
| `BATCH_SIZE` | 5 | blocks per batch |
| `START_BLOCKS_BACK` | 1000 | where to start on an empty DB |
| `MAX_RPS` | 10 | RPC requests per second limit |
| `WHALE_ETH` | 100 | ETH threshold |
| `WHALE_STABLE_USD` | 1000000 | USDT/USDC threshold |
| `INDEXER_HTTP_PORT` | 9100 | indexer `/metrics` and `/healthz` |
| `API_PORT` | 8000 | API port |

## API

| Endpoint | |
|---|---|
| `GET /gas` | latest base fee + last hour avg/min/max |
| `GET /blocks/latest?limit=10` | latest blocks |
| `GET /whales?limit=20&token=USDT` | latest whale transfers (`ETH`, `USDT`, `USDC`) |
| `GET /status` | indexed block range and row counts |
| `GET /healthz`, `/readyz` | liveness / readiness (DB reachable) |
| `GET /metrics` | Prometheus metrics |

## Metrics

- `ethidx_lag_blocks` — blocks behind the chain head. **Main health signal.**
- `ethidx_chain_head_block`, `ethidx_last_indexed_block`
- `ethidx_rpc_requests_total{method,endpoint,status}` — multiply by the provider's compute-unit
  cost per method to estimate quota usage
- `ethidx_rpc_failovers_total`, `ethidx_reorgs_total`, `ethidx_loop_errors_total`
- `ethidx_whale_transfers_total{token}`, `ethidx_blocks_processed_total`, `ethidx_batch_seconds`

RPC URLs often contain an API key, so logs and metrics only ever show the host name.

## Run locally

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
export DATABASE_URL=postgresql://user:pass@localhost:5432/indexer
export RPC_URLS=https://your-rpc-url
PYTHONPATH=src python -m ethidx indexer   # in one terminal
PYTHONPATH=src python -m ethidx api       # in another, then: curl localhost:8000/gas
```

## CI

`.github/workflows/build.yml` builds the image on every push to `main` and pushes
`feliswiedii/eth-indexer:sha-<commit>` and `:latest` to Docker Hub.
