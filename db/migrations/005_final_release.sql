-- v13 final release: revocable browser sessions, moderation decisions and release evidence hardening.

CREATE TABLE auth_sessions (
  id uuid PRIMARY KEY,
  user_id uuid NOT NULL REFERENCES users(id),
  refresh_token_hash char(64) NOT NULL UNIQUE,
  csrf_token_hash char(64) NOT NULL,
  user_agent_hash char(64),
  ip_hash char(64),
  created_at timestamptz NOT NULL DEFAULT now(),
  last_seen_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL,
  revoked_at timestamptz,
  revoke_reason text
);
CREATE INDEX ix_auth_sessions_user_active ON auth_sessions(user_id,expires_at) WHERE revoked_at IS NULL;
CREATE INDEX ix_auth_sessions_expiry ON auth_sessions(expires_at);

CREATE TABLE moderation_decisions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id uuid NOT NULL REFERENCES workspaces(id),
  subject_type text NOT NULL CHECK(subject_type IN ('song_spec','generation_quote','asset','offer','brand_submission')),
  subject_id text NOT NULL,
  policy_version text NOT NULL,
  status text NOT NULL CHECK(status IN ('allow','review','block')),
  reasons jsonb NOT NULL DEFAULT '[]'::jsonb,
  input_hash char(64) NOT NULL,
  decided_by text NOT NULL DEFAULT 'deterministic-policy',
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_moderation_decisions_subject ON moderation_decisions(workspace_id,subject_type,subject_id,created_at DESC);

ALTER TABLE moderation_decisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE moderation_decisions FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_moderation_decisions ON moderation_decisions
  USING(workspace_id::text=current_setting('app.workspace_id',true))
  WITH CHECK(workspace_id::text=current_setting('app.workspace_id',true));

GRANT SELECT,INSERT,UPDATE ON auth_sessions TO music_app;
GRANT SELECT,INSERT ON moderation_decisions TO music_app;
GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO music_app,music_worker;

-- Sessions and moderation evidence are append-preserved except explicit revocation/last-seen updates.
CREATE OR REPLACE FUNCTION guard_auth_session_update() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.id<>NEW.id OR OLD.user_id<>NEW.user_id OR OLD.refresh_token_hash<>NEW.refresh_token_hash
     OR OLD.csrf_token_hash<>NEW.csrf_token_hash OR OLD.created_at<>NEW.created_at
     OR OLD.expires_at<>NEW.expires_at THEN
    RAISE EXCEPTION 'immutable auth session fields cannot change';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER auth_session_update_guard BEFORE UPDATE ON auth_sessions
FOR EACH ROW EXECUTE FUNCTION guard_auth_session_update();

CREATE OR REPLACE FUNCTION prevent_moderation_decision_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'moderation decisions are immutable'; END $$;
CREATE TRIGGER immutable_moderation_decision_update BEFORE UPDATE OR DELETE ON moderation_decisions
FOR EACH ROW EXECUTE FUNCTION prevent_moderation_decision_mutation();

INSERT INTO release_evidence(gate,evidence_key,status,evidence,owner)
VALUES
 ('G13','session_revocation','in_progress','{}'::jsonb,'security'),
 ('G13','moderation_preflight','in_progress','{}'::jsonb,'trust'),
 ('G13','unified_gateway','in_progress','{}'::jsonb,'platform'),
 ('G13','provider_contract','missing','{}'::jsonb,'provider'),
 ('G13','docker_e2e','missing','{}'::jsonb,'quality'),
 ('G13','backup_restore','missing','{}'::jsonb,'sre')
ON CONFLICT(gate,evidence_key) DO NOTHING;
