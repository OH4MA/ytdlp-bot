-- Initial schema for yt-dlp Bot state store.

CREATE TABLE schema_migrations (
  version INTEGER PRIMARY KEY CHECK (version > 0),
  name TEXT NOT NULL,
  checksum TEXT NOT NULL,
  applied_at INTEGER NOT NULL
) STRICT;

CREATE TABLE service_state (
  singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
  settings_revision INTEGER NOT NULL CHECK (settings_revision >= 0),
  artifact_catalog_revision INTEGER NOT NULL CHECK (artifact_catalog_revision >= 0),
  storage_accounting_revision INTEGER NOT NULL CHECK (storage_accounting_revision >= 0),
  storage_epoch INTEGER NOT NULL CHECK (storage_epoch >= 0),
  last_cleanup_at INTEGER,
  last_cleanup_error TEXT,
  updated_at INTEGER NOT NULL
) STRICT;

INSERT INTO service_state (
  singleton, settings_revision, artifact_catalog_revision,
  storage_accounting_revision, storage_epoch, updated_at
) VALUES (1, 0, 0, 0, 0, 0);

CREATE TABLE jobs (
  job_id TEXT PRIMARY KEY,
  owner_platform TEXT NOT NULL CHECK (owner_platform IN ('telegram', 'discord')),
  owner_user_id TEXT NOT NULL,
  request_mode TEXT NOT NULL CHECK (request_mode IN ('video', 'audio')),
  selected_preset TEXT NOT NULL,
  source_display TEXT NOT NULL,
  media_kind TEXT NOT NULL CHECK (media_kind IN ('unknown', 'single', 'playlist')),
  state TEXT NOT NULL CHECK (state IN (
    'queued', 'inspecting', 'downloading', 'post_processing', 'archiving',
    'delivering', 'cancelling', 'completed', 'completed_with_errors', 'failed',
    'cancelled', 'cancelled_by_restart', 'expired', 'evicted'
  )),
  completion_outcome TEXT CHECK (
    completion_outcome IS NULL OR completion_outcome IN ('completed', 'completed_with_errors')
  ),
  context_json TEXT NOT NULL,
  acknowledged_at INTEGER,
  dispatchable INTEGER NOT NULL CHECK (dispatchable IN (0, 1)),
  cancellation_requested INTEGER NOT NULL CHECK (cancellation_requested IN (0, 1)),
  controller_instance_id TEXT,
  worker_instance_id TEXT,
  worker_lease_expires_at INTEGER,
  last_worker_sequence INTEGER NOT NULL CHECK (last_worker_sequence >= 0),
  progress_json TEXT,
  warning_codes_json TEXT NOT NULL DEFAULT '[]',
  error_code TEXT,
  error_detail TEXT,
  version INTEGER NOT NULL CHECK (version >= 1),
  created_at INTEGER NOT NULL,
  started_at INTEGER,
  ready_at INTEGER,
  terminal_at INTEGER,
  updated_at INTEGER NOT NULL,
  CHECK (dispatchable = 0 OR (acknowledged_at IS NOT NULL AND context_json IS NOT NULL))
) STRICT;

CREATE INDEX idx_jobs_owner_recent
  ON jobs (owner_platform, owner_user_id, created_at DESC);
CREATE INDEX idx_jobs_queue
  ON jobs (state, dispatchable, created_at, job_id);
CREATE INDEX idx_jobs_worker_lease
  ON jobs (worker_lease_expires_at);
CREATE INDEX idx_jobs_terminal
  ON jobs (terminal_at);

CREATE TABLE command_requests (
  platform TEXT NOT NULL CHECK (platform IN ('telegram', 'discord')),
  platform_request_id TEXT NOT NULL,
  command_name TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('processing', 'completed', 'failed')),
  job_id TEXT REFERENCES jobs(job_id) ON DELETE SET NULL,
  result_summary_json TEXT,
  created_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL,
  PRIMARY KEY (platform, platform_request_id)
) STRICT;

CREATE TABLE job_payloads (
  job_id TEXT PRIMARY KEY REFERENCES jobs(job_id) ON DELETE CASCADE,
  source_url TEXT NOT NULL CHECK (length(source_url) > 0 AND length(source_url) <= 4096),
  created_at INTEGER NOT NULL
) STRICT;

CREATE TABLE job_events (
  event_id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
  from_state TEXT,
  to_state TEXT NOT NULL,
  reason_code TEXT,
  occurred_at INTEGER NOT NULL
) STRICT;

CREATE TABLE playlist_entries (
  job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
  playlist_index INTEGER NOT NULL CHECK (playlist_index >= 1),
  extractor_source_id TEXT,
  sanitized_title TEXT,
  generated_output_name TEXT,
  state TEXT NOT NULL CHECK (state IN (
    'pending', 'downloading', 'post_processing', 'succeeded', 'failed', 'cancelled'
  )),
  byte_size INTEGER CHECK (byte_size IS NULL OR byte_size >= 0),
  failure_code TEXT,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (job_id, playlist_index),
  UNIQUE (job_id, generated_output_name)
) STRICT;

CREATE INDEX idx_playlist_state
  ON playlist_entries (job_id, state, playlist_index);

CREATE TABLE artifacts (
  artifact_id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL UNIQUE REFERENCES jobs(job_id) ON DELETE CASCADE,
  storage_key TEXT NOT NULL UNIQUE,
  display_name TEXT NOT NULL,
  media_type TEXT NOT NULL CHECK (media_type IN (
    'video/mp4', 'audio/mpeg', 'application/zip'
  )),
  byte_size INTEGER NOT NULL CHECK (byte_size > 0),
  access_state TEXT NOT NULL CHECK (access_state IN (
    'available', 'deletion_pending', 'deleted'
  )),
  deletion_reason TEXT CHECK (
    deletion_reason IS NULL OR deletion_reason IN (
      'expired', 'evicted', 'administrator', 'job_cancelled', 'reconciliation'
    )
  ),
  token_version INTEGER NOT NULL CHECK (token_version >= 1),
  ready_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL,
  deletion_attempts INTEGER NOT NULL DEFAULT 0 CHECK (deletion_attempts >= 0),
  next_deletion_attempt_at INTEGER,
  last_deletion_error TEXT,
  deleted_at INTEGER,
  updated_at INTEGER NOT NULL,
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  CHECK (expires_at >= ready_at)
) STRICT;

CREATE INDEX idx_artifacts_expiry
  ON artifacts (expires_at, artifact_id) WHERE access_state = 'available';
CREATE INDEX idx_artifacts_ready
  ON artifacts (ready_at, artifact_id) WHERE access_state = 'available';
CREATE INDEX idx_artifacts_deletion
  ON artifacts (next_deletion_attempt_at) WHERE access_state = 'deletion_pending';
CREATE INDEX idx_artifacts_deleted
  ON artifacts (deleted_at);

CREATE TABLE capacity_reservations (
  job_id TEXT PRIMARY KEY REFERENCES jobs(job_id) ON DELETE CASCADE,
  reserved_bytes INTEGER NOT NULL CHECK (reserved_bytes >= 0),
  observed_workspace_bytes INTEGER NOT NULL CHECK (observed_workspace_bytes >= 0),
  controller_instance_id TEXT NOT NULL,
  expires_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
) STRICT;

CREATE TABLE runtime_settings (
  setting_key TEXT PRIMARY KEY,
  value_json TEXT NOT NULL,
  revision INTEGER NOT NULL CHECK (revision >= 1),
  updated_by_platform TEXT NOT NULL CHECK (updated_by_platform IN ('telegram', 'discord')),
  updated_by_user_id TEXT NOT NULL,
  updated_at INTEGER NOT NULL
) STRICT;

CREATE TABLE whitelist (
  platform TEXT NOT NULL CHECK (platform IN ('telegram', 'discord')),
  user_id TEXT NOT NULL,
  created_by_platform TEXT NOT NULL CHECK (created_by_platform IN ('telegram', 'discord')),
  created_by_user_id TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  PRIMARY KEY (platform, user_id)
) STRICT;

CREATE TABLE admin_confirmations (
  confirmation_digest BLOB PRIMARY KEY,
  admin_platform TEXT NOT NULL CHECK (admin_platform IN ('telegram', 'discord')),
  admin_user_id TEXT NOT NULL,
  action TEXT NOT NULL CHECK (action IN ('capacity_set', 'capacity_reset')),
  payload_json TEXT NOT NULL,
  settings_revision INTEGER NOT NULL,
  storage_epoch INTEGER NOT NULL,
  projected_count INTEGER NOT NULL CHECK (projected_count >= 0),
  projected_bytes INTEGER NOT NULL CHECK (projected_bytes >= 0),
  expires_at INTEGER NOT NULL,
  consumed_at INTEGER,
  created_at INTEGER NOT NULL
) STRICT;

CREATE TABLE delivery_attempts (
  attempt_id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
  artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id) ON DELETE CASCADE,
  method TEXT NOT NULL CHECK (method IN ('direct_upload', 'signed_link')),
  status TEXT NOT NULL CHECK (status IN (
    'started', 'succeeded', 'too_large', 'transient_failed', 'permanent_failed', 'uncertain'
  )),
  platform_operation_id TEXT,
  link_expires_at INTEGER,
  error_code TEXT,
  started_at INTEGER NOT NULL,
  finished_at INTEGER
) STRICT;

CREATE TABLE platform_notifications (
  notification_id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL UNIQUE REFERENCES jobs(job_id) ON DELETE CASCADE,
  artifact_id TEXT REFERENCES artifacts(artifact_id) ON DELETE SET NULL,
  notification_kind TEXT NOT NULL CHECK (notification_kind IN (
    'final_summary', 'signed_link_result', 'failure_result'
  )),
  payload_safe_json TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('pending', 'sending', 'sent', 'failed')),
  generation INTEGER NOT NULL CHECK (generation >= 1),
  attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  next_attempt_at INTEGER,
  last_error_code TEXT,
  created_at INTEGER NOT NULL,
  sent_at INTEGER
) STRICT;
