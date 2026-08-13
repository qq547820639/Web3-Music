CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE workspaces (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL,
  plan text NOT NULL DEFAULT 'trial',
  status text NOT NULL DEFAULT 'active' CHECK(status IN ('active','suspended','closed')),
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE users (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email text NOT NULL UNIQUE,
  display_name text NOT NULL,
  password_hash text NOT NULL,
  status text NOT NULL DEFAULT 'active' CHECK(status IN ('active','disabled')),
  is_platform_admin boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE workspace_members (
  workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role text NOT NULL CHECK(role IN ('owner','admin','creator','reviewer','viewer','billing','legal','support')),
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(workspace_id,user_id)
);
CREATE INDEX ix_members_user ON workspace_members(user_id,workspace_id);

CREATE TABLE provider_capability_snapshots (
  id uuid PRIMARY KEY,
  provider text NOT NULL,
  adapter_version text NOT NULL,
  environment text NOT NULL,
  approval_status text NOT NULL,
  snapshot jsonb NOT NULL,
  contract_version text,
  captured_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE song_projects (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id uuid NOT NULL REFERENCES workspaces(id),
  title text NOT NULL,
  status text NOT NULL DEFAULT 'active' CHECK(status IN ('active','archived','legal_hold')),
  current_revision integer NOT NULL DEFAULT 0,
  locked_paths jsonb NOT NULL DEFAULT '[]'::jsonb,
  created_by uuid NOT NULL REFERENCES users(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(workspace_id,id)
);
CREATE INDEX ix_projects_workspace ON song_projects(workspace_id,created_at DESC);

CREATE TABLE song_spec_revisions (
  workspace_id uuid NOT NULL REFERENCES workspaces(id),
  project_id uuid NOT NULL REFERENCES song_projects(id) ON DELETE CASCADE,
  revision integer NOT NULL,
  parent_revision integer,
  spec jsonb NOT NULL,
  spec_hash char(64) NOT NULL,
  command_id uuid,
  actor_type text NOT NULL CHECK(actor_type IN ('user','ai','system')),
  actor_id text NOT NULL,
  reason text,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(project_id,revision),
  UNIQUE(project_id,spec_hash),
  UNIQUE(workspace_id,project_id,revision),
  FOREIGN KEY(workspace_id,project_id) REFERENCES song_projects(workspace_id,id)
);

CREATE TABLE contribution_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id uuid NOT NULL REFERENCES workspaces(id),
  project_id uuid NOT NULL REFERENCES song_projects(id) ON DELETE CASCADE,
  spec_revision integer,
  actor_type text NOT NULL,
  actor_id text NOT NULL,
  event_type text NOT NULL,
  target_paths jsonb NOT NULL DEFAULT '[]'::jsonb,
  evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY(workspace_id,project_id) REFERENCES song_projects(workspace_id,id)
);

CREATE TABLE quality_evaluations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id uuid NOT NULL REFERENCES workspaces(id),
  project_id uuid NOT NULL,
  spec_revision integer NOT NULL,
  engine_version text NOT NULL,
  ruleset_version text NOT NULL,
  input_hash char(64) NOT NULL,
  score numeric(6,2) NOT NULL,
  grade text NOT NULL,
  dimensions jsonb NOT NULL,
  variables jsonb NOT NULL,
  risks jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(project_id,spec_revision,engine_version,ruleset_version),
  FOREIGN KEY(project_id,spec_revision) REFERENCES song_spec_revisions(project_id,revision),
  FOREIGN KEY(workspace_id,project_id,spec_revision) REFERENCES song_spec_revisions(workspace_id,project_id,revision)
);

CREATE TABLE generation_quotes (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id uuid NOT NULL REFERENCES workspaces(id),
  project_id uuid NOT NULL REFERENCES song_projects(id),
  spec_revision integer NOT NULL,
  provider_snapshot_id uuid NOT NULL REFERENCES provider_capability_snapshots(id),
  candidate_count integer NOT NULL CHECK(candidate_count BETWEEN 1 AND 8),
  unit_credits integer NOT NULL CHECK(unit_credits>0),
  total_credits integer NOT NULL,
  scenario text NOT NULL DEFAULT 'success',
  quote jsonb NOT NULL,
  quote_hash char(64) NOT NULL,
  expires_at timestamptz NOT NULL,
  accepted_at timestamptz,
  created_by uuid NOT NULL REFERENCES users(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK(total_credits=unit_credits*candidate_count),
  UNIQUE(workspace_id,id),
  FOREIGN KEY(project_id,spec_revision) REFERENCES song_spec_revisions(project_id,revision),
  FOREIGN KEY(workspace_id,project_id,spec_revision) REFERENCES song_spec_revisions(workspace_id,project_id,revision)
);

CREATE TABLE ledger_accounts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id uuid NOT NULL REFERENCES workspaces(id),
  account_type text NOT NULL CHECK(account_type IN ('available','held','expense','refunds','issued')),
  currency text NOT NULL DEFAULT 'CREDIT',
  UNIQUE(workspace_id,account_type,currency),
  UNIQUE(workspace_id,id)
);

CREATE TABLE ledger_transactions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id uuid NOT NULL REFERENCES workspaces(id),
  operation_key text NOT NULL,
  transaction_type text NOT NULL CHECK(transaction_type IN ('seed','hold','settle','release','refund','adjustment','reversal')),
  reference_type text NOT NULL,
  reference_id text NOT NULL,
  reversal_of uuid REFERENCES ledger_transactions(id),
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_by text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(workspace_id,operation_key),
  UNIQUE(workspace_id,id)
);

CREATE TABLE ledger_entries (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id uuid NOT NULL REFERENCES workspaces(id),
  transaction_id uuid NOT NULL REFERENCES ledger_transactions(id),
  account_id uuid NOT NULL REFERENCES ledger_accounts(id),
  delta numeric(20,6) NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_ledger_entries_account ON ledger_entries(account_id,created_at DESC);

CREATE OR REPLACE FUNCTION validate_ledger_entry_workspace() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE tx_ws uuid; account_ws uuid;
BEGIN
  SELECT workspace_id INTO tx_ws FROM ledger_transactions WHERE id=NEW.transaction_id;
  SELECT workspace_id INTO account_ws FROM ledger_accounts WHERE id=NEW.account_id;
  IF tx_ws IS NULL OR account_ws IS NULL OR NEW.workspace_id<>tx_ws OR NEW.workspace_id<>account_ws THEN
    RAISE EXCEPTION 'ledger workspace mismatch';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER validate_ledger_workspace BEFORE INSERT ON ledger_entries FOR EACH ROW EXECUTE FUNCTION validate_ledger_entry_workspace();

CREATE OR REPLACE FUNCTION validate_ledger_balance() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE total numeric;
BEGIN
  SELECT COALESCE(SUM(delta),0) INTO total FROM ledger_entries WHERE transaction_id=NEW.transaction_id;
  IF total<>0 THEN RAISE EXCEPTION 'ledger transaction % is unbalanced: %',NEW.transaction_id,total; END IF;
  RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER ledger_must_balance AFTER INSERT ON ledger_entries DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION validate_ledger_balance();

CREATE OR REPLACE FUNCTION prevent_ledger_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'ledger records are append-only'; END $$;
CREATE TRIGGER no_update_ledger_entries BEFORE UPDATE OR DELETE ON ledger_entries FOR EACH ROW EXECUTE FUNCTION prevent_ledger_mutation();
CREATE TRIGGER no_update_ledger_transactions BEFORE UPDATE OR DELETE ON ledger_transactions FOR EACH ROW EXECUTE FUNCTION prevent_ledger_mutation();

CREATE OR REPLACE FUNCTION prevent_immutable_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION '% is immutable',TG_TABLE_NAME; END $$;

CREATE VIEW ledger_balances WITH (security_invoker=true) AS
SELECT a.workspace_id,a.account_type,a.currency,COALESCE(SUM(e.delta),0)::numeric(20,6) AS balance
FROM ledger_accounts a LEFT JOIN ledger_entries e ON e.account_id=a.id
GROUP BY a.workspace_id,a.account_type,a.currency;

CREATE TABLE credit_holds (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id uuid NOT NULL REFERENCES workspaces(id),
  quote_id uuid NOT NULL UNIQUE REFERENCES generation_quotes(id),
  original_amount numeric(20,6) NOT NULL CHECK(original_amount>0),
  settled_amount numeric(20,6) NOT NULL DEFAULT 0 CHECK(settled_amount>=0),
  released_amount numeric(20,6) NOT NULL DEFAULT 0 CHECK(released_amount>=0),
  status text NOT NULL DEFAULT 'active' CHECK(status IN ('active','partially_settled','settled','released')),
  expires_at timestamptz NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK(settled_amount+released_amount<=original_amount),
  UNIQUE(workspace_id,id),
  FOREIGN KEY(workspace_id,quote_id) REFERENCES generation_quotes(workspace_id,id),
  CHECK((status='active' AND settled_amount+released_amount<original_amount) OR
        (status='settled' AND settled_amount=original_amount AND released_amount=0) OR
        (status='released' AND released_amount=original_amount AND settled_amount=0) OR
        (status='partially_settled' AND settled_amount>0 AND released_amount>0 AND settled_amount+released_amount=original_amount))
);

CREATE TABLE generation_jobs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id uuid NOT NULL REFERENCES workspaces(id),
  project_id uuid NOT NULL REFERENCES song_projects(id),
  spec_revision integer NOT NULL,
  quote_id uuid NOT NULL REFERENCES generation_quotes(id),
  hold_id uuid NOT NULL UNIQUE REFERENCES credit_holds(id),
  provider_snapshot_id uuid NOT NULL REFERENCES provider_capability_snapshots(id),
  idempotency_key text NOT NULL,
  request_hash char(64) NOT NULL,
  status text NOT NULL CHECK(status IN ('queued','leased','submitting','provider_queued','processing','ingesting','completed','partial','retry_wait','failed','dead_letter','cancel_requested','cancelled')),
  requested_candidates integer NOT NULL,
  provider_job_id text,
  provider_status text,
  settled_credits integer NOT NULL DEFAULT 0,
  actual_provider_cost numeric(18,6),
  lease_owner text,
  lease_expires_at timestamptz,
  heartbeat_at timestamptz,
  attempt_count integer NOT NULL DEFAULT 0,
  max_attempts integer NOT NULL DEFAULT 4,
  next_attempt_at timestamptz NOT NULL DEFAULT now(),
  error jsonb,
  created_by uuid NOT NULL REFERENCES users(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  started_at timestamptz,
  completed_at timestamptz,
  UNIQUE(workspace_id,idempotency_key),
  UNIQUE(workspace_id,id),
  FOREIGN KEY(project_id,spec_revision) REFERENCES song_spec_revisions(project_id,revision),
  FOREIGN KEY(workspace_id,project_id,spec_revision) REFERENCES song_spec_revisions(workspace_id,project_id,revision),
  FOREIGN KEY(workspace_id,quote_id) REFERENCES generation_quotes(workspace_id,id),
  FOREIGN KEY(workspace_id,hold_id) REFERENCES credit_holds(workspace_id,id)
);
CREATE INDEX ix_jobs_claim ON generation_jobs(status,next_attempt_at,lease_expires_at,created_at);

CREATE TABLE generation_steps (
  id bigserial PRIMARY KEY,
  workspace_id uuid NOT NULL REFERENCES workspaces(id),
  job_id uuid NOT NULL REFERENCES generation_jobs(id) ON DELETE CASCADE,
  step_name text NOT NULL,
  operation_key text NOT NULL,
  status text NOT NULL CHECK(status IN ('started','completed','failed','compensated')),
  attempt integer NOT NULL,
  input_hash char(64),
  output jsonb NOT NULL DEFAULT '{}'::jsonb,
  error jsonb,
  started_at timestamptz NOT NULL DEFAULT now(),
  ended_at timestamptz,
  UNIQUE(job_id,operation_key),
  FOREIGN KEY(workspace_id,job_id) REFERENCES generation_jobs(workspace_id,id)
);

CREATE TABLE generation_attempts (
  id bigserial PRIMARY KEY,
  workspace_id uuid NOT NULL REFERENCES workspaces(id),
  job_id uuid NOT NULL REFERENCES generation_jobs(id) ON DELETE CASCADE,
  attempt integer NOT NULL,
  lease_owner text NOT NULL,
  outcome text NOT NULL,
  error_class text,
  payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  started_at timestamptz NOT NULL DEFAULT now(),
  ended_at timestamptz,
  UNIQUE(job_id,attempt),
  FOREIGN KEY(workspace_id,job_id) REFERENCES generation_jobs(workspace_id,id)
);

CREATE TABLE media_assets (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id uuid NOT NULL REFERENCES workspaces(id),
  kind text NOT NULL,
  bucket text NOT NULL,
  object_key text NOT NULL,
  sha256 char(64) NOT NULL,
  mime_type text NOT NULL,
  bytes bigint NOT NULL CHECK(bytes>0),
  duration_ms bigint,
  scan_status text NOT NULL CHECK(scan_status IN ('pending','clean','rejected')),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(bucket,object_key),
  UNIQUE(workspace_id,sha256,kind),
  UNIQUE(workspace_id,id)
);

CREATE TABLE audio_candidates (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id uuid NOT NULL REFERENCES workspaces(id),
  job_id uuid NOT NULL REFERENCES generation_jobs(id) ON DELETE CASCADE,
  ordinal integer NOT NULL,
  provider_clip_id text NOT NULL,
  status text NOT NULL CHECK(status IN ('ready','failed')),
  media_asset_id uuid REFERENCES media_assets(id),
  recipe jsonb NOT NULL,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK((status='ready' AND media_asset_id IS NOT NULL) OR status='failed'),
  UNIQUE(job_id,ordinal),
  UNIQUE(job_id,provider_clip_id),
  UNIQUE(workspace_id,id),
  FOREIGN KEY(workspace_id,job_id) REFERENCES generation_jobs(workspace_id,id),
  FOREIGN KEY(workspace_id,media_asset_id) REFERENCES media_assets(workspace_id,id)
);

CREATE TABLE master_selections (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id uuid NOT NULL REFERENCES workspaces(id),
  project_id uuid NOT NULL REFERENCES song_projects(id),
  candidate_id uuid NOT NULL REFERENCES audio_candidates(id),
  spec_revision integer NOT NULL,
  selected_by uuid NOT NULL REFERENCES users(id),
  replaced_selection_id uuid REFERENCES master_selections(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(workspace_id,id),
  FOREIGN KEY(workspace_id,project_id) REFERENCES song_projects(workspace_id,id),
  FOREIGN KEY(workspace_id,candidate_id) REFERENCES audio_candidates(workspace_id,id),
  FOREIGN KEY(workspace_id,replaced_selection_id) REFERENCES master_selections(workspace_id,id)
);

CREATE TABLE asset_snapshots (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id uuid NOT NULL REFERENCES workspaces(id),
  project_id uuid NOT NULL REFERENCES song_projects(id),
  master_selection_id uuid NOT NULL UNIQUE REFERENCES master_selections(id),
  candidate_id uuid NOT NULL REFERENCES audio_candidates(id),
  spec_revision integer NOT NULL,
  snapshot jsonb NOT NULL,
  snapshot_hash char(64) NOT NULL UNIQUE,
  media_hash char(64) NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(workspace_id,id),
  FOREIGN KEY(workspace_id,project_id) REFERENCES song_projects(workspace_id,id),
  FOREIGN KEY(workspace_id,master_selection_id) REFERENCES master_selections(workspace_id,id),
  FOREIGN KEY(workspace_id,candidate_id) REFERENCES audio_candidates(workspace_id,id)
);

CREATE TABLE rights_manifests (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id uuid NOT NULL REFERENCES workspaces(id),
  asset_snapshot_id uuid NOT NULL REFERENCES asset_snapshots(id),
  version integer NOT NULL,
  status text NOT NULL,
  provider text NOT NULL,
  provider_approval text NOT NULL,
  manifest jsonb NOT NULL,
  manifest_hash char(64) NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(asset_snapshot_id,version),
  FOREIGN KEY(workspace_id,asset_snapshot_id) REFERENCES asset_snapshots(workspace_id,id)
);

CREATE TABLE domain_outbox (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id uuid,
  aggregate_type text NOT NULL,
  aggregate_id text NOT NULL,
  event_type text NOT NULL,
  event_version integer NOT NULL DEFAULT 1,
  payload jsonb NOT NULL,
  occurred_at timestamptz NOT NULL DEFAULT now(),
  published_at timestamptz,
  publish_attempts integer NOT NULL DEFAULT 0,
  last_error text
);
CREATE INDEX ix_outbox_unpublished ON domain_outbox(occurred_at) WHERE published_at IS NULL;

CREATE TABLE provider_inbox (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  provider text NOT NULL,
  event_id text NOT NULL,
  event_type text NOT NULL,
  signature_valid boolean NOT NULL,
  payload jsonb NOT NULL,
  received_at timestamptz NOT NULL DEFAULT now(),
  processed_at timestamptz,
  UNIQUE(provider,event_id)
);

CREATE TABLE system_settings (
  key text PRIMARY KEY,
  value jsonb NOT NULL,
  updated_by text NOT NULL DEFAULT 'system',
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE audit_events (
  id bigserial PRIMARY KEY,
  workspace_id uuid,
  actor_id text NOT NULL,
  actor_role text,
  action text NOT NULL,
  subject_type text NOT NULL,
  subject_id text,
  request_id text,
  ip_hash text,
  payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_audit_workspace ON audit_events(workspace_id,created_at DESC);

CREATE OR REPLACE FUNCTION validate_credit_hold_quote() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE amount numeric; quote_ws uuid;
BEGIN
  SELECT total_credits,workspace_id INTO amount,quote_ws FROM generation_quotes WHERE id=NEW.quote_id;
  IF quote_ws IS NULL OR quote_ws<>NEW.workspace_id OR amount<>NEW.original_amount THEN
    RAISE EXCEPTION 'credit hold does not match quote';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER credit_hold_matches_quote BEFORE INSERT ON credit_holds FOR EACH ROW EXECUTE FUNCTION validate_credit_hold_quote();

CREATE OR REPLACE FUNCTION validate_generation_job_quote() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE q_project uuid; q_revision integer; q_provider uuid; q_count integer; q_ws uuid; h_quote uuid;
BEGIN
  SELECT project_id,spec_revision,provider_snapshot_id,candidate_count,workspace_id INTO q_project,q_revision,q_provider,q_count,q_ws FROM generation_quotes WHERE id=NEW.quote_id;
  SELECT quote_id INTO h_quote FROM credit_holds WHERE id=NEW.hold_id;
  IF q_ws IS NULL OR q_ws<>NEW.workspace_id OR q_project<>NEW.project_id OR q_revision<>NEW.spec_revision OR q_provider<>NEW.provider_snapshot_id OR q_count<>NEW.requested_candidates OR h_quote<>NEW.quote_id THEN
    RAISE EXCEPTION 'generation job does not match quote/hold';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER generation_job_matches_quote BEFORE INSERT ON generation_jobs FOR EACH ROW EXECUTE FUNCTION validate_generation_job_quote();

CREATE OR REPLACE FUNCTION validate_master_selection() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE c_project uuid; c_revision integer; old_project uuid;
BEGIN
  SELECT j.project_id,j.spec_revision INTO c_project,c_revision FROM audio_candidates c JOIN generation_jobs j ON j.id=c.job_id WHERE c.id=NEW.candidate_id AND c.workspace_id=NEW.workspace_id AND c.status='ready';
  IF c_project IS NULL OR c_project<>NEW.project_id OR c_revision<>NEW.spec_revision THEN RAISE EXCEPTION 'master candidate/project/revision mismatch'; END IF;
  IF NEW.replaced_selection_id IS NOT NULL THEN
    SELECT project_id INTO old_project FROM master_selections WHERE id=NEW.replaced_selection_id AND workspace_id=NEW.workspace_id;
    IF old_project IS NULL OR old_project<>NEW.project_id THEN RAISE EXCEPTION 'replacement master mismatch'; END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER master_selection_consistent BEFORE INSERT ON master_selections FOR EACH ROW EXECUTE FUNCTION validate_master_selection();

CREATE OR REPLACE FUNCTION validate_asset_snapshot() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE m_project uuid; m_candidate uuid; m_revision integer;
BEGIN
  SELECT project_id,candidate_id,spec_revision INTO m_project,m_candidate,m_revision FROM master_selections WHERE id=NEW.master_selection_id AND workspace_id=NEW.workspace_id;
  IF m_project IS NULL OR m_project<>NEW.project_id OR m_candidate<>NEW.candidate_id OR m_revision<>NEW.spec_revision THEN RAISE EXCEPTION 'asset snapshot does not match master selection'; END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER asset_snapshot_consistent BEFORE INSERT ON asset_snapshots FOR EACH ROW EXECUTE FUNCTION validate_asset_snapshot();

CREATE TRIGGER immutable_song_revisions BEFORE UPDATE OR DELETE ON song_spec_revisions FOR EACH ROW EXECUTE FUNCTION prevent_immutable_mutation();
CREATE TRIGGER immutable_contributions BEFORE UPDATE OR DELETE ON contribution_events FOR EACH ROW EXECUTE FUNCTION prevent_immutable_mutation();
CREATE TRIGGER immutable_provider_snapshots BEFORE UPDATE OR DELETE ON provider_capability_snapshots FOR EACH ROW EXECUTE FUNCTION prevent_immutable_mutation();
CREATE TRIGGER immutable_candidates BEFORE UPDATE OR DELETE ON audio_candidates FOR EACH ROW EXECUTE FUNCTION prevent_immutable_mutation();
CREATE TRIGGER immutable_master_selections BEFORE UPDATE OR DELETE ON master_selections FOR EACH ROW EXECUTE FUNCTION prevent_immutable_mutation();
CREATE TRIGGER immutable_asset_snapshots BEFORE UPDATE OR DELETE ON asset_snapshots FOR EACH ROW EXECUTE FUNCTION prevent_immutable_mutation();
CREATE TRIGGER immutable_rights_manifests BEFORE UPDATE OR DELETE ON rights_manifests FOR EACH ROW EXECUTE FUNCTION prevent_immutable_mutation();
CREATE TRIGGER immutable_audit_events BEFORE UPDATE OR DELETE ON audit_events FOR EACH ROW EXECUTE FUNCTION prevent_immutable_mutation();

-- Tenant isolation. The API role must set app.workspace_id in every domain transaction.
DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY[
    'song_projects','song_spec_revisions','contribution_events','quality_evaluations','generation_quotes',
    'ledger_accounts','ledger_transactions','ledger_entries','credit_holds','generation_jobs','generation_steps',
    'generation_attempts','media_assets','audio_candidates','master_selections','asset_snapshots','rights_manifests'
  ] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',t);
    EXECUTE format('CREATE POLICY tenant_policy ON %I USING (workspace_id::text=current_setting(''app.workspace_id'',true)) WITH CHECK (workspace_id::text=current_setting(''app.workspace_id'',true))',t);
  END LOOP;
END $$;

-- Seed data. Passwords: demo-owner / demo-creator / demo-viewer.
INSERT INTO workspaces(id,name,plan) VALUES
('11111111-1111-1111-1111-111111111111','Demo Workspace','trial'),
('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa','Other Workspace','trial')
ON CONFLICT DO NOTHING;

INSERT INTO users(id,email,display_name,password_hash,is_platform_admin) VALUES
('22222222-2222-2222-2222-222222222222','owner@example.local','Demo Owner','pbkdf2_sha256$200000$IaE1cMJvq28nXC80L+QDFg==$OpPrpAb2lLPrdsua7vVYjxgkJ0h/lXWr4kDNu72QP80=',true),
('44444444-4444-4444-4444-444444444444','creator@example.local','Demo Creator','pbkdf2_sha256$200000$zW0qXaIBb6SAW4TKxouiug==$dG0ZbHwa6rvtR7t6rFea200T/yUHFeO9VVePJsGBDB8=',false),
('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb','viewer@other.local','Other Viewer','pbkdf2_sha256$200000$ZqW4CkyAIk65Cg5ebbDXyw==$A/i3PK8dd22PrI7wQG43zMILS0c9J+7nhVnCkMEKqW4=',false)
ON CONFLICT DO NOTHING;

INSERT INTO workspace_members(workspace_id,user_id,role) VALUES
('11111111-1111-1111-1111-111111111111','22222222-2222-2222-2222-222222222222','owner'),
('11111111-1111-1111-1111-111111111111','44444444-4444-4444-4444-444444444444','creator'),
('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa','bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb','owner')
ON CONFLICT DO NOTHING;

INSERT INTO provider_capability_snapshots(id,provider,adapter_version,environment,approval_status,snapshot,contract_version)
VALUES('33333333-3333-3333-3333-333333333333','emulator','2.0.0','local','development_only',
'{"candidate_count_max":8,"async":true,"commercial_rights":"blocked","supports_custom_lyrics":true,"supports_styles":true,"supports_cancel":true,"supports_webhooks":true}'::jsonb,'emulator-local-v1')
ON CONFLICT(id) DO NOTHING;

INSERT INTO system_settings(key,value) VALUES
('generation_enabled','true'::jsonb),('provider_enabled','true'::jsonb),('exports_enabled','true'::jsonb)
ON CONFLICT DO NOTHING;

-- Seed ledger accounts and balanced credits for both workspaces.
INSERT INTO ledger_accounts(workspace_id,account_type)
SELECT w.id,t.account_type FROM workspaces w CROSS JOIN (VALUES('available'),('held'),('expense'),('refunds'),('issued')) t(account_type)
ON CONFLICT DO NOTHING;

DO $$ DECLARE ws uuid; tx uuid; avail uuid; issued uuid; BEGIN
  FOR ws IN SELECT id FROM workspaces LOOP
    IF NOT EXISTS (SELECT 1 FROM ledger_transactions WHERE workspace_id=ws AND operation_key='seed:initial') THEN
      tx:=gen_random_uuid();
      SELECT id INTO avail FROM ledger_accounts WHERE workspace_id=ws AND account_type='available';
      SELECT id INTO issued FROM ledger_accounts WHERE workspace_id=ws AND account_type='issued';
      INSERT INTO ledger_transactions(id,workspace_id,operation_key,transaction_type,reference_type,reference_id,created_by)
      VALUES(tx,ws,'seed:initial','seed','workspace',ws::text,'migration');
      INSERT INTO ledger_entries(workspace_id,transaction_id,account_id,delta) VALUES
      (ws,tx,avail,1000),(ws,tx,issued,-1000);
    END IF;
  END LOOP;
END $$;

GRANT USAGE ON SCHEMA public TO music_app,music_worker;

GRANT SELECT ON users,workspaces,workspace_members TO music_app;
GRANT SELECT,INSERT ON provider_capability_snapshots TO music_app;
GRANT SELECT,INSERT,UPDATE ON song_projects,generation_quotes,quality_evaluations,credit_holds,generation_jobs,system_settings TO music_app;
GRANT SELECT,INSERT ON song_spec_revisions,contribution_events,ledger_transactions,ledger_entries,master_selections,asset_snapshots,rights_manifests,domain_outbox,provider_inbox,audit_events TO music_app;
GRANT SELECT ON ledger_accounts,ledger_balances,generation_steps,generation_attempts,media_assets,audio_candidates TO music_app;

GRANT SELECT ON provider_capability_snapshots,generation_quotes,song_spec_revisions,ledger_accounts,ledger_balances TO music_worker;
GRANT SELECT,UPDATE ON generation_jobs,credit_holds,provider_inbox,domain_outbox TO music_worker;
GRANT SELECT,INSERT,UPDATE ON generation_steps,generation_attempts,media_assets TO music_worker;
GRANT SELECT,INSERT ON ledger_transactions,ledger_entries,audio_candidates TO music_worker;
GRANT INSERT ON domain_outbox TO music_worker;

GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO music_app,music_worker;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE,SELECT ON SEQUENCES TO music_app,music_worker;
