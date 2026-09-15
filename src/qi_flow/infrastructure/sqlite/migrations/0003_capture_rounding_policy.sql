ALTER TABLE work_sessions ADD COLUMN rounding_minutes INTEGER NOT NULL DEFAULT 5
    CHECK (rounding_minutes IN (1, 5, 10, 15));

ALTER TABLE deductions ADD COLUMN rounding_minutes INTEGER NOT NULL DEFAULT 5
    CHECK (rounding_minutes IN (1, 5, 10, 15));
