"""All settings are read from environment variables."""

import os
from dataclasses import dataclass, field


def _env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None or value == "":
        raise RuntimeError(f"environment variable {name} is required")
    return value


@dataclass(frozen=True)
class Config:
    database_url: str = field(default_factory=lambda: _env("DATABASE_URL"))

    # Comma-separated RPC URLs. First is primary, rest are failovers.
    rpc_urls: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            u.strip() for u in os.environ.get("RPC_URLS", "").split(",") if u.strip()
        )
    )

    # Process blocks only after this many confirmations (reorg protection)
    confirmations: int = int(os.environ.get("CONFIRMATIONS", "5"))
    poll_interval: float = float(os.environ.get("POLL_INTERVAL", "6"))
    batch_size: int = int(os.environ.get("BATCH_SIZE", "5"))
    # On empty DB, start this many blocks back instead of from genesis
    start_blocks_back: int = int(os.environ.get("START_BLOCKS_BACK", "1000"))
    max_rps: float = float(os.environ.get("MAX_RPS", "10"))

    whale_eth: float = float(os.environ.get("WHALE_ETH", "100"))
    whale_stable_usd: float = float(os.environ.get("WHALE_STABLE_USD", "1000000"))

    indexer_http_port: int = int(os.environ.get("INDEXER_HTTP_PORT", "9100"))
    api_port: int = int(os.environ.get("API_PORT", "8000"))

    log_level: str = os.environ.get("LOG_LEVEL", "INFO")
