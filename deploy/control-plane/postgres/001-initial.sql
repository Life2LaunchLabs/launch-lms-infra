CREATE TABLE IF NOT EXISTS projects (
  id varchar(64) PRIMARY KEY,
  manifest_revision varchar(64) NOT NULL,
  enabled boolean NOT NULL DEFAULT true,
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS embed_sessions (
  id uuid PRIMARY KEY,
  project_id varchar(64) NOT NULL REFERENCES projects(id),
  environment varchar(32) NOT NULL,
  opaque_user_id varchar(128) NOT NULL,
  opaque_org_id varchar(128) NOT NULL,
  nonce_hash char(64) NOT NULL UNIQUE,
  expires_at timestamptz NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS unread_markers (
  id bigserial PRIMARY KEY,
  project_id varchar(64) NOT NULL REFERENCES projects(id),
  environment varchar(32) NOT NULL,
  opaque_user_id varchar(128) NOT NULL,
  conversation_id varchar(128) NOT NULL,
  seen_revision varchar(128) NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(project_id, environment, opaque_user_id, conversation_id)
);
CREATE TABLE IF NOT EXISTS sync_cursors (
  id bigserial PRIMARY KEY,
  project_id varchar(64) NOT NULL REFERENCES projects(id),
  adapter varchar(64) NOT NULL,
  stream varchar(128) NOT NULL,
  cursor text,
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(project_id, adapter, stream)
);
CREATE TABLE IF NOT EXISTS idempotency_records (
  id bigserial PRIMARY KEY,
  project_id varchar(64) NOT NULL REFERENCES projects(id),
  operation varchar(64) NOT NULL,
  key_hash char(64) NOT NULL,
  status varchar(32) NOT NULL,
  result jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(project_id, operation, key_hash)
);
CREATE TABLE IF NOT EXISTS agent_runs (
  id uuid PRIMARY KEY,
  project_id varchar(64) NOT NULL REFERENCES projects(id),
  issue_key varchar(64) NOT NULL,
  attempt integer NOT NULL,
  workspace varchar(255) NOT NULL,
  policy_revision varchar(64) NOT NULL,
  workflow_hash char(64) NOT NULL,
  base_sha char(40) NOT NULL,
  head_sha char(40),
  turns integer NOT NULL DEFAULT 0,
  duration_ms bigint,
  checks jsonb NOT NULL DEFAULT '{}',
  evidence jsonb NOT NULL DEFAULT '{}',
  deployment_result jsonb,
  retry_reason text,
  disposition varchar(32) NOT NULL,
  started_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz,
  UNIQUE(project_id, issue_key, attempt)
);
CREATE INDEX IF NOT EXISTS agent_run_issue ON agent_runs(issue_key);
CREATE TABLE IF NOT EXISTS deployment_observations (
  id bigserial PRIMARY KEY,
  project_id varchar(64) NOT NULL REFERENCES projects(id),
  environment varchar(32) NOT NULL,
  source_sha char(40) NOT NULL,
  image_digest varchar(80) NOT NULL,
  workflow_run_id varchar(64) NOT NULL,
  status varchar(32) NOT NULL,
  observed_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS announcements (
  id uuid PRIMARY KEY,
  project_id varchar(64) NOT NULL REFERENCES projects(id),
  environment varchar(32) NOT NULL,
  title varchar(200) NOT NULL,
  body text NOT NULL,
  published_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);
