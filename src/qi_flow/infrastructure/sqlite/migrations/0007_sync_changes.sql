-- Append-only protocol history is independent of the 30-day recovery audit.
-- Nothing in this migration enables publication or infers a reviewed baseline.
CREATE TABLE sync_changes (
    spreadsheet_id TEXT NOT NULL,
    log_id TEXT NOT NULL,
    change_id TEXT NOT NULL,
    canonical_json TEXT NOT NULL,
    PRIMARY KEY (spreadsheet_id, log_id, change_id)
);

CREATE TABLE sync_outbox (
    spreadsheet_id TEXT NOT NULL,
    log_id TEXT NOT NULL,
    change_id TEXT NOT NULL,
    not_before_utc TEXT,
    attempted INTEGER NOT NULL DEFAULT 0 CHECK (attempted IN (0, 1)),
    PRIMARY KEY (spreadsheet_id, log_id, change_id),
    FOREIGN KEY (spreadsheet_id, log_id, change_id)
        REFERENCES sync_changes(spreadsheet_id, log_id, change_id)
);

CREATE TABLE sync_acknowledgements (
    spreadsheet_id TEXT NOT NULL,
    log_id TEXT NOT NULL,
    change_id TEXT NOT NULL,
    PRIMARY KEY (spreadsheet_id, log_id, change_id),
    FOREIGN KEY (spreadsheet_id, log_id, change_id)
        REFERENCES sync_outbox(spreadsheet_id, log_id, change_id)
);

CREATE TABLE sync_heads (
    spreadsheet_id TEXT NOT NULL,
    log_id TEXT NOT NULL,
    entity_kind TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    head_ids_json TEXT NOT NULL,
    PRIMARY KEY (spreadsheet_id, log_id, entity_kind, entity_id)
);

CREATE TABLE sync_conflicts (
    spreadsheet_id TEXT NOT NULL,
    log_id TEXT NOT NULL,
    conflict_id TEXT NOT NULL,
    content_json TEXT NOT NULL,
    closed INTEGER NOT NULL DEFAULT 0 CHECK (closed IN (0, 1)),
    PRIMARY KEY (spreadsheet_id, log_id, conflict_id)
);

CREATE TABLE sync_state (
    spreadsheet_id TEXT NOT NULL,
    log_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value_json TEXT NOT NULL,
    PRIMARY KEY (spreadsheet_id, log_id, key)
);

CREATE TABLE sync_problems (
    spreadsheet_id TEXT NOT NULL,
    log_id TEXT NOT NULL,
    problem_id TEXT NOT NULL,
    content_json TEXT NOT NULL,
    PRIMARY KEY (spreadsheet_id, log_id, problem_id)
);
