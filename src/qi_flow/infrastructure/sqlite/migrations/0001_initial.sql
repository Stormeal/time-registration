CREATE TABLE work_sessions (
  id TEXT PRIMARY KEY,
  actual_started_at_utc TEXT NOT NULL,
  actual_ended_at_utc TEXT,
  effective_started_at_utc TEXT,
  effective_ended_at_utc TEXT,
  source TEXT NOT NULL CHECK (source IN ('timer', 'manual', 'recovery')),
  revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
  created_at_utc TEXT NOT NULL,
  updated_at_utc TEXT NOT NULL,
  deleted_at_utc TEXT,
  CHECK ((effective_started_at_utc IS NULL) = (effective_ended_at_utc IS NULL))
);

CREATE UNIQUE INDEX one_active_work_session
ON work_sessions ((1))
WHERE actual_ended_at_utc IS NULL AND deleted_at_utc IS NULL;

CREATE INDEX work_sessions_actual_range
ON work_sessions (actual_started_at_utc, actual_ended_at_utc)
WHERE deleted_at_utc IS NULL;

CREATE TABLE deductions (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES work_sessions(id),
  kind TEXT NOT NULL CHECK (kind IN ('lunch', 'sleep_break')),
  actual_started_at_utc TEXT NOT NULL,
  actual_ended_at_utc TEXT,
  effective_started_at_utc TEXT,
  effective_ended_at_utc TEXT,
  source TEXT NOT NULL CHECK (source IN ('timer', 'manual', 'recovery')),
  revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
  created_at_utc TEXT NOT NULL,
  updated_at_utc TEXT NOT NULL,
  deleted_at_utc TEXT,
  CHECK ((effective_started_at_utc IS NULL) = (effective_ended_at_utc IS NULL))
);

CREATE UNIQUE INDEX one_active_deduction
ON deductions ((1))
WHERE actual_ended_at_utc IS NULL AND deleted_at_utc IS NULL;

CREATE INDEX deductions_by_session
ON deductions (session_id, actual_started_at_utc)
WHERE deleted_at_utc IS NULL;

CREATE TABLE day_details (
  work_date TEXT PRIMARY KEY,
  location TEXT NOT NULL DEFAULT 'remote' CHECK (location IN ('remote', 'office')),
  note TEXT NOT NULL DEFAULT '',
  revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
  updated_at_utc TEXT NOT NULL
);

CREATE TABLE weekly_targets (
  iso_year INTEGER NOT NULL,
  iso_week INTEGER NOT NULL CHECK (iso_week BETWEEN 1 AND 53),
  target_minutes INTEGER NOT NULL CHECK (target_minutes >= 0),
  updated_at_utc TEXT NOT NULL,
  PRIMARY KEY (iso_year, iso_week)
);

CREATE TABLE settings (
  key TEXT PRIMARY KEY,
  value_json TEXT NOT NULL,
  updated_at_utc TEXT NOT NULL
);

CREATE TABLE audit_entries (
  id TEXT PRIMARY KEY,
  entity_type TEXT NOT NULL CHECK (entity_type IN ('work_session', 'deduction', 'day_details')),
  entity_id TEXT NOT NULL,
  action TEXT NOT NULL CHECK (action IN ('update', 'delete', 'undo')),
  before_state_json TEXT NOT NULL,
  created_at_utc TEXT NOT NULL,
  expires_at_utc TEXT NOT NULL
);

CREATE INDEX audit_entries_expiry ON audit_entries (expires_at_utc);

