"""Database schema and queries."""

SCHEMA = """
CREATE TABLE IF NOT EXISTS blocks (
    number        BIGINT PRIMARY KEY,
    hash          TEXT NOT NULL,
    parent_hash   TEXT NOT NULL,
    ts            TIMESTAMPTZ NOT NULL,
    tx_count      INTEGER NOT NULL,
    gas_used      BIGINT NOT NULL,
    gas_limit     BIGINT NOT NULL,
    base_fee_gwei NUMERIC(20, 9)
);
CREATE INDEX IF NOT EXISTS blocks_ts_idx ON blocks (ts);

CREATE TABLE IF NOT EXISTS transfers (
    id           BIGSERIAL PRIMARY KEY,
    block_number BIGINT NOT NULL,
    ts           TIMESTAMPTZ NOT NULL,
    tx_hash      TEXT NOT NULL,
    log_index    INTEGER NOT NULL,          -- -1 for ETH transfers (no event log)
    token        TEXT NOT NULL,             -- ETH / USDT / USDC
    from_addr    TEXT NOT NULL,
    to_addr      TEXT,
    amount       NUMERIC(38, 6) NOT NULL,
    UNIQUE (tx_hash, log_index)
);
CREATE INDEX IF NOT EXISTS transfers_ts_idx ON transfers (ts DESC);
CREATE INDEX IF NOT EXISTS transfers_block_idx ON transfers (block_number);
"""

LAST_BLOCK = "SELECT number, hash FROM blocks ORDER BY number DESC LIMIT 1"

INSERT_BLOCK = """
INSERT INTO blocks (number, hash, parent_hash, ts, tx_count, gas_used, gas_limit, base_fee_gwei)
VALUES (%(number)s, %(hash)s, %(parent_hash)s, to_timestamp(%(ts)s), %(tx_count)s,
        %(gas_used)s, %(gas_limit)s, %(base_fee_gwei)s)
ON CONFLICT (number) DO NOTHING
"""

INSERT_TRANSFER = """
INSERT INTO transfers (block_number, ts, tx_hash, log_index, token, from_addr, to_addr, amount)
VALUES (%(block_number)s, to_timestamp(%(ts)s), %(tx_hash)s, %(log_index)s, %(token)s,
        %(from_addr)s, %(to_addr)s, %(amount)s)
ON CONFLICT (tx_hash, log_index) DO NOTHING
"""

DELETE_FROM_BLOCK = [
    "DELETE FROM transfers WHERE block_number >= %s",
    "DELETE FROM blocks WHERE number >= %s",
]
