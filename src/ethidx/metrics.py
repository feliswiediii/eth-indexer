"""Метрики Prometheus. Их соберёт мониторинг (VictoriaMetrics / Prometheus)."""

from prometheus_client import Counter, Gauge, Histogram

CHAIN_HEAD = Gauge("ethidx_chain_head_block", "Latest block number reported by RPC")
LAST_INDEXED = Gauge("ethidx_last_indexed_block", "Last block saved to the database")
LAG = Gauge("ethidx_lag_blocks", "How many blocks the indexer is behind the chain head")

BLOCKS = Counter("ethidx_blocks_processed_total", "Blocks processed")
TRANSFERS = Counter("ethidx_whale_transfers_total", "Whale transfers saved", ["token"])
REORGS = Counter("ethidx_reorgs_total", "Reorgs detected (parent hash mismatch)")
BATCH_SECONDS = Histogram("ethidx_batch_seconds", "Time to process one batch of blocks")
ERRORS = Counter("ethidx_loop_errors_total", "Errors in the main indexer loop")

# Запросы к RPC. Для оценки расхода лимита провайдера умножай на стоимость метода
# (compute units) из документации провайдера.
RPC_REQUESTS = Counter("ethidx_rpc_requests_total", "RPC calls", ["method", "endpoint", "status"])
RPC_LATENCY = Histogram("ethidx_rpc_latency_seconds", "RPC HTTP request latency", ["endpoint"])
RPC_FAILOVERS = Counter("ethidx_rpc_failovers_total", "Switches to another RPC endpoint")
