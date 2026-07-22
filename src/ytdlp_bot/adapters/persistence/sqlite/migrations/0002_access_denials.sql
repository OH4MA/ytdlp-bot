-- Temporary store of unauthorized access attempts for admin whitelist review.

CREATE TABLE access_denials (
  platform TEXT NOT NULL CHECK (platform IN ('telegram', 'discord')),
  user_id TEXT NOT NULL,
  first_seen_at INTEGER NOT NULL,
  last_seen_at INTEGER NOT NULL,
  attempt_count INTEGER NOT NULL CHECK (attempt_count >= 1),
  last_command TEXT,
  PRIMARY KEY (platform, user_id)
) STRICT;

CREATE INDEX idx_access_denials_last_seen
  ON access_denials (last_seen_at DESC);
