CREATE TABLE IF NOT EXISTS workspace_members (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  workspace_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  role TEXT NOT NULL DEFAULT 'member',
  created_at TEXT NOT NULL,
  UNIQUE(workspace_id, user_id),
  FOREIGN KEY(workspace_id) REFERENCES workspaces(id),
  FOREIGN KEY(user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS share_links (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  token TEXT NOT NULL UNIQUE,
  workspace_id INTEGER,
  owner_user_id INTEGER NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  expires_at TEXT,
  resolved_count INTEGER NOT NULL DEFAULT 0,
  last_resolved_at TEXT,
  FOREIGN KEY(workspace_id) REFERENCES workspaces(id),
  FOREIGN KEY(owner_user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS investigation_sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  workspace_id INTEGER,
  owner_user_id INTEGER NOT NULL,
  title TEXT NOT NULL,
  notes TEXT,
  state_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  expires_at TEXT,
  FOREIGN KEY(workspace_id) REFERENCES workspaces(id),
  FOREIGN KEY(owner_user_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_workspace_members_workspace ON workspace_members(workspace_id);
CREATE INDEX IF NOT EXISTS idx_workspace_members_user ON workspace_members(user_id);
CREATE INDEX IF NOT EXISTS idx_share_links_owner ON share_links(owner_user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_share_links_workspace ON share_links(workspace_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_investigation_owner ON investigation_sessions(owner_user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_investigation_workspace ON investigation_sessions(workspace_id, updated_at DESC);
