CREATE TABLE IF NOT EXISTS workspaces (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(user_id, name),
  FOREIGN KEY(user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS audit_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  action TEXT NOT NULL,
  target TEXT,
  status_code INTEGER,
  metadata_json TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(user_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_scans_user_created_at ON scans(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_user_updated_at ON jobs(user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_user_created_at ON audit_logs(user_id, created_at DESC);
