-- Reconstructed from the INSERTs/SELECTs in app/tracker/tracker.py and app/tracker/metrics.py.
-- Every statement is IF NOT EXISTS, so running it against your existing Neon database
-- changes nothing that already exists. Run it in a fresh database to get a working gateway.

CREATE TABLE IF NOT EXISTS request_logs (
    id               BIGSERIAL PRIMARY KEY,
    timestamp        TIMESTAMPTZ NOT NULL DEFAULT now(),
    user_id          TEXT,
    conversation_id  TEXT,
    provider_used    TEXT,
    model_used       TEXT,
    was_fallback     BOOLEAN NOT NULL DEFAULT FALSE,
    cost_usd         DOUBLE PRECISION NOT NULL DEFAULT 0,
    latency_ms       DOUBLE PRECISION,
    estimated_tokens INTEGER,
    cache_hit        BOOLEAN NOT NULL DEFAULT FALSE,
    cache_scope      TEXT,
    status           TEXT NOT NULL DEFAULT 'success',
    error_type       TEXT,
    embed_latency_ms DOUBLE PRECISION,
    embed_cost_usd   DOUBLE PRECISION,
    input_tokens     INTEGER,
    output_tokens    INTEGER,
    thought_tokens   INTEGER,
    cached_tokens    INTEGER
);
CREATE INDEX IF NOT EXISTS request_logs_timestamp_idx ON request_logs (timestamp);

ALTER TABLE request_logs ADD COLUMN IF NOT EXISTS request_id       TEXT;
ALTER TABLE request_logs ADD COLUMN IF NOT EXISTS deployment_id    TEXT;
ALTER TABLE request_logs ADD COLUMN IF NOT EXISTS attempts         SMALLINT;
ALTER TABLE request_logs ADD COLUMN IF NOT EXISTS capacity_existed BOOLEAN;
CREATE INDEX IF NOT EXISTS request_logs_request_id_idx ON request_logs (request_id);
CREATE INDEX IF NOT EXISTS request_logs_deployment_time_idx ON request_logs (deployment_id, timestamp);

CREATE TABLE IF NOT EXISTS attempt_logs (
    id             BIGSERIAL PRIMARY KEY,
    timestamp      TIMESTAMPTZ NOT NULL DEFAULT now(),
    request_id     TEXT NOT NULL,
    attempt_no     SMALLINT NOT NULL,
    deployment_id  TEXT NOT NULL,
    provider       TEXT NOT NULL,
    model          TEXT NOT NULL,
    outcome        TEXT NOT NULL,
    error_type     TEXT,
    latency_ms     DOUBLE PRECISION,
    retry_after_s  DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS attempt_logs_timestamp_idx ON attempt_logs (timestamp);
CREATE INDEX IF NOT EXISTS attempt_logs_deployment_time_idx ON attempt_logs (deployment_id, timestamp);
CREATE INDEX IF NOT EXISTS attempt_logs_request_id_idx ON attempt_logs (request_id);
ALTER TABLE attempt_logs ADD COLUMN IF NOT EXISTS error_detail TEXT;

CREATE TABLE IF NOT EXISTS breaker_events (
    id         BIGSERIAL PRIMARY KEY,
    timestamp  TIMESTAMPTZ NOT NULL DEFAULT now(),
    provider   TEXT NOT NULL,
    old_state  TEXT NOT NULL,
    new_state  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS metric_snapshots (
    id           BIGSERIAL PRIMARY KEY,
    label        TEXT NOT NULL,
    taken_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    window_since TIMESTAMPTZ,
    window_until TIMESTAMPTZ,
    metrics      JSONB NOT NULL
);
