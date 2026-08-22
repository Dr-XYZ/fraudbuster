-- schema.sql - Cloudflare D1 Database Schema for Fraudbuster

CREATE TABLE IF NOT EXISTS reports (
    url TEXT PRIMARY KEY,
    username TEXT,
    case_id TEXT,
    reported_at TEXT
);

CREATE TABLE IF NOT EXISTS cases (
    url TEXT PRIMARY KEY,
    username TEXT,
    case_id TEXT,
    reported_at TEXT,
    fraudbuster_url TEXT,
    fb_is_final INTEGER DEFAULT 0,
    timeline_stages TEXT,
    threads_actual_status TEXT DEFAULT 'Active',
    first_checked_at TEXT,
    last_checked_at TEXT,
    takedown_detected_at TEXT,
    check_count INTEGER DEFAULT 0,
    check_history TEXT,
    updated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_cases_status ON cases(threads_actual_status);
CREATE INDEX IF NOT EXISTS idx_cases_last_checked ON cases(last_checked_at);
