-- AI Music Asset Platform v12: Creation OS, Asset OS, Market OS.
-- Additive migration. Existing immutable generation and ledger contracts remain authoritative.

-- Composite keys required by tenant-safe foreign keys introduced below.
ALTER TABLE rights_manifests ADD CONSTRAINT rights_manifests_workspace_id_key UNIQUE(workspace_id,id);
ALTER TABLE rights_manifests ADD CONSTRAINT rights_manifests_workspace_asset_id_key UNIQUE(workspace_id,id,asset_snapshot_id);

CREATE TABLE project_branches (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id uuid NOT NULL REFERENCES workspaces(id),
  project_id uuid NOT NULL REFERENCES song_projects(id) ON DELETE CASCADE,
  name text NOT NULL CHECK(length(name) BETWEEN 1 AND 80),
  head_revision integer NOT NULL,
  base_revision integer NOT NULL,
  created_by uuid NOT NULL REFERENCES users(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(project_id,name),
  UNIQUE(workspace_id,id),
  FOREIGN KEY(workspace_id,project_id,head_revision) REFERENCES song_spec_revisions(workspace_id,project_id,revision),
  FOREIGN KEY(workspace_id,project_id,base_revision) REFERENCES song_spec_revisions(workspace_id,project_id,revision)
);

CREATE TABLE project_comments (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id uuid NOT NULL REFERENCES workspaces(id),
  project_id uuid NOT NULL REFERENCES song_projects(id) ON DELETE CASCADE,
  spec_revision integer,
  candidate_id uuid,
  timecode_ms bigint CHECK(timecode_ms IS NULL OR timecode_ms>=0),
  body text NOT NULL CHECK(length(body) BETWEEN 1 AND 4000),
  status text NOT NULL DEFAULT 'open' CHECK(status IN ('open','resolved')),
  created_by uuid NOT NULL REFERENCES users(id),
  resolved_by uuid REFERENCES users(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  resolved_at timestamptz,
  UNIQUE(workspace_id,id),
  FOREIGN KEY(workspace_id,project_id) REFERENCES song_projects(workspace_id,id),
  FOREIGN KEY(workspace_id,project_id,spec_revision) REFERENCES song_spec_revisions(workspace_id,project_id,revision),
  FOREIGN KEY(workspace_id,candidate_id) REFERENCES audio_candidates(workspace_id,id)
);

CREATE TABLE user_preferences (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id uuid NOT NULL REFERENCES workspaces(id),
  user_id uuid NOT NULL REFERENCES users(id),
  scope text NOT NULL CHECK(scope IN ('global','workspace','project')),
  project_id uuid,
  preferences jsonb NOT NULL DEFAULT '{}'::jsonb,
  learning_enabled boolean NOT NULL DEFAULT true,
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(workspace_id,id),
  FOREIGN KEY(workspace_id,project_id) REFERENCES song_projects(workspace_id,id),
  CHECK((scope='project' AND project_id IS NOT NULL) OR (scope<>'project' AND project_id IS NULL))
);
CREATE UNIQUE INDEX ux_preferences_nonproject ON user_preferences(workspace_id,user_id,scope) WHERE project_id IS NULL;
CREATE UNIQUE INDEX ux_preferences_project ON user_preferences(workspace_id,user_id,scope,project_id) WHERE project_id IS NOT NULL;

CREATE TABLE product_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id uuid NOT NULL REFERENCES workspaces(id),
  user_id uuid REFERENCES users(id),
  project_id uuid,
  asset_snapshot_id uuid,
  event_name text NOT NULL,
  event_version integer NOT NULL DEFAULT 1,
  properties jsonb NOT NULL DEFAULT '{}'::jsonb,
  occurred_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(workspace_id,id),
  FOREIGN KEY(workspace_id,project_id) REFERENCES song_projects(workspace_id,id),
  FOREIGN KEY(workspace_id,asset_snapshot_id) REFERENCES asset_snapshots(workspace_id,id)
);
CREATE INDEX ix_product_events_metric ON product_events(workspace_id,event_name,occurred_at DESC);

CREATE TABLE ai_registries (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  kind text NOT NULL CHECK(kind IN ('prompt','model','tool_schema','eval','quality_ruleset')),
  key text NOT NULL,
  version text NOT NULL,
  status text NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','active','retired')),
  definition jsonb NOT NULL,
  checksum char(64) NOT NULL,
  created_by text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(kind,key,version)
);

CREATE TABLE ai_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id uuid NOT NULL REFERENCES workspaces(id),
  project_id uuid,
  spec_revision integer,
  purpose text NOT NULL,
  provider text NOT NULL,
  model text NOT NULL,
  prompt_version text NOT NULL,
  schema_version text NOT NULL,
  input_hash char(64) NOT NULL,
  output_hash char(64),
  status text NOT NULL CHECK(status IN ('succeeded','failed','fallback')),
  latency_ms integer,
  input_tokens integer,
  output_tokens integer,
  estimated_cost numeric(18,8),
  safety jsonb NOT NULL DEFAULT '{}'::jsonb,
  error jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(workspace_id,id),
  FOREIGN KEY(workspace_id,project_id) REFERENCES song_projects(workspace_id,id)
);

CREATE TABLE moderation_cases (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id uuid NOT NULL REFERENCES workspaces(id),
  subject_type text NOT NULL CHECK(subject_type IN ('project','candidate','asset','user','brand_brief')),
  subject_id text NOT NULL,
  case_type text NOT NULL CHECK(case_type IN ('copyright','voice_likeness','trademark','harassment','minor_safety','malware','other')),
  status text NOT NULL DEFAULT 'open' CHECK(status IN ('open','triage','restricted','appealed','resolved','dismissed')),
  severity text NOT NULL DEFAULT 'medium' CHECK(severity IN ('low','medium','high','critical')),
  restrictions jsonb NOT NULL DEFAULT '{}'::jsonb,
  evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
  legal_hold boolean NOT NULL DEFAULT false,
  opened_by text NOT NULL,
  assigned_to uuid REFERENCES users(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  closed_at timestamptz,
  UNIQUE(workspace_id,id)
);

CREATE TABLE rights_evidence (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id uuid NOT NULL REFERENCES workspaces(id),
  project_id uuid,
  asset_snapshot_id uuid,
  evidence_type text NOT NULL CHECK(evidence_type IN ('user_declaration','license','consent','provider_contract','source_file','identity','other')),
  status text NOT NULL DEFAULT 'submitted' CHECK(status IN ('submitted','verified','rejected','expired')),
  evidence jsonb NOT NULL,
  media_asset_id uuid,
  submitted_by uuid NOT NULL REFERENCES users(id),
  reviewed_by uuid REFERENCES users(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  reviewed_at timestamptz,
  expires_at timestamptz,
  UNIQUE(workspace_id,id),
  FOREIGN KEY(workspace_id,project_id) REFERENCES song_projects(workspace_id,id),
  FOREIGN KEY(workspace_id,asset_snapshot_id) REFERENCES asset_snapshots(workspace_id,id),
  FOREIGN KEY(workspace_id,media_asset_id) REFERENCES media_assets(workspace_id,id)
);

CREATE TABLE product_catalog (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  sku text NOT NULL UNIQUE,
  product_type text NOT NULL CHECK(product_type IN ('credit_pack','subscription','license_template','service')),
  name text NOT NULL,
  description text NOT NULL DEFAULT '',
  currency char(3) NOT NULL DEFAULT 'USD',
  unit_amount bigint NOT NULL CHECK(unit_amount>=0),
  credits integer,
  active boolean NOT NULL DEFAULT true,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE orders (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id uuid NOT NULL REFERENCES workspaces(id),
  order_number text NOT NULL UNIQUE,
  order_type text NOT NULL CHECK(order_type IN ('credit_purchase','subscription','license','brand_task')),
  status text NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','payment_pending','paid','fulfilled','cancelled','refunded','disputed')),
  currency char(3) NOT NULL,
  subtotal bigint NOT NULL CHECK(subtotal>=0),
  tax bigint NOT NULL DEFAULT 0 CHECK(tax>=0),
  total bigint NOT NULL CHECK(total>=0),
  customer_user_id uuid NOT NULL REFERENCES users(id),
  subject_type text,
  subject_id text,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(workspace_id,id),
  CHECK(total=subtotal+tax)
);

CREATE TABLE order_items (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id uuid NOT NULL REFERENCES workspaces(id),
  order_id uuid NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
  catalog_id uuid REFERENCES product_catalog(id),
  description text NOT NULL,
  quantity integer NOT NULL CHECK(quantity>0),
  unit_amount bigint NOT NULL CHECK(unit_amount>=0),
  total bigint NOT NULL CHECK(total>=0),
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE(workspace_id,id),
  FOREIGN KEY(workspace_id,order_id) REFERENCES orders(workspace_id,id),
  CHECK(total=quantity*unit_amount)
);

CREATE TABLE payments (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id uuid NOT NULL REFERENCES workspaces(id),
  order_id uuid NOT NULL REFERENCES orders(id),
  provider text NOT NULL,
  provider_payment_id text,
  idempotency_key text NOT NULL,
  request_hash char(64) NOT NULL,
  status text NOT NULL CHECK(status IN ('created','requires_action','processing','succeeded','failed','cancelled','refunded','partially_refunded')),
  currency char(3) NOT NULL,
  amount bigint NOT NULL CHECK(amount>=0),
  refunded_amount bigint NOT NULL DEFAULT 0 CHECK(refunded_amount>=0 AND refunded_amount<=amount),
  raw jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(workspace_id,id),
  UNIQUE(workspace_id,idempotency_key),
  UNIQUE(provider,provider_payment_id),
  FOREIGN KEY(workspace_id,order_id) REFERENCES orders(workspace_id,id)
);

CREATE TABLE payment_inbox (
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

CREATE TABLE refunds (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id uuid NOT NULL REFERENCES workspaces(id),
  order_id uuid NOT NULL REFERENCES orders(id),
  payment_id uuid NOT NULL REFERENCES payments(id),
  idempotency_key text NOT NULL,
  request_hash char(64) NOT NULL,
  amount bigint NOT NULL CHECK(amount>0),
  reason text NOT NULL,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  status text NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','processing','succeeded','failed')),
  provider_refund_id text,
  created_by uuid NOT NULL REFERENCES users(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(workspace_id,id),
  UNIQUE(workspace_id,idempotency_key),
  FOREIGN KEY(workspace_id,order_id) REFERENCES orders(workspace_id,id),
  FOREIGN KEY(workspace_id,payment_id) REFERENCES payments(workspace_id,id)
);

CREATE TABLE subscriptions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id uuid NOT NULL REFERENCES workspaces(id),
  order_id uuid NOT NULL UNIQUE REFERENCES orders(id),
  catalog_id uuid NOT NULL REFERENCES product_catalog(id),
  status text NOT NULL CHECK(status IN ('trialing','active','past_due','paused','cancelled')),
  current_period_start timestamptz NOT NULL,
  current_period_end timestamptz NOT NULL,
  cancel_at_period_end boolean NOT NULL DEFAULT false,
  provider_subscription_id text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(workspace_id,id)
);

CREATE TABLE license_templates (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id uuid,
  name text NOT NULL,
  version integer NOT NULL,
  status text NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','active','retired')),
  terms jsonb NOT NULL,
  terms_hash char(64) NOT NULL,
  created_by text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(workspace_id,name,version)
);

CREATE TABLE asset_offers (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id uuid NOT NULL REFERENCES workspaces(id),
  asset_snapshot_id uuid NOT NULL REFERENCES asset_snapshots(id),
  rights_manifest_id uuid NOT NULL REFERENCES rights_manifests(id),
  license_template_id uuid NOT NULL REFERENCES license_templates(id),
  title text NOT NULL,
  description text NOT NULL DEFAULT '',
  price_amount bigint NOT NULL CHECK(price_amount>=0),
  currency char(3) NOT NULL DEFAULT 'USD',
  territory text NOT NULL DEFAULT 'worldwide',
  duration_days integer,
  exclusive boolean NOT NULL DEFAULT false,
  status text NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','active','paused','sold','withdrawn')),
  created_by uuid NOT NULL REFERENCES users(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(workspace_id,id),
  FOREIGN KEY(workspace_id,asset_snapshot_id) REFERENCES asset_snapshots(workspace_id,id),
  FOREIGN KEY(workspace_id,rights_manifest_id,asset_snapshot_id) REFERENCES rights_manifests(workspace_id,id,asset_snapshot_id)
);

CREATE TABLE offer_reservations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  offer_id uuid NOT NULL REFERENCES asset_offers(id),
  buyer_workspace_id uuid NOT NULL REFERENCES workspaces(id),
  order_id uuid NOT NULL UNIQUE REFERENCES orders(id),
  status text NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','confirmed','released','expired')),
  expires_at timestamptz NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(buyer_workspace_id,id),
  FOREIGN KEY(buyer_workspace_id,order_id) REFERENCES orders(workspace_id,id)
);
CREATE INDEX ix_offer_reservations_active ON offer_reservations(offer_id,status,expires_at);

CREATE TABLE licenses (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  seller_workspace_id uuid NOT NULL REFERENCES workspaces(id),
  buyer_workspace_id uuid NOT NULL REFERENCES workspaces(id),
  order_id uuid NOT NULL UNIQUE REFERENCES orders(id),
  asset_snapshot_id uuid NOT NULL REFERENCES asset_snapshots(id),
  rights_manifest_id uuid NOT NULL REFERENCES rights_manifests(id),
  license_template_id uuid NOT NULL REFERENCES license_templates(id),
  status text NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','active','suspended','expired','revoked','refunded')),
  licensee_name text NOT NULL,
  territory text NOT NULL,
  starts_at timestamptz,
  ends_at timestamptz,
  terms_snapshot jsonb NOT NULL,
  license_hash char(64) NOT NULL UNIQUE,
  created_at timestamptz NOT NULL DEFAULT now(),
  activated_at timestamptz,
  UNIQUE(seller_workspace_id,id),
  UNIQUE(buyer_workspace_id,id),
  FOREIGN KEY(seller_workspace_id,asset_snapshot_id) REFERENCES asset_snapshots(workspace_id,id),
  FOREIGN KEY(seller_workspace_id,rights_manifest_id,asset_snapshot_id) REFERENCES rights_manifests(workspace_id,id,asset_snapshot_id),
  FOREIGN KEY(buyer_workspace_id,order_id) REFERENCES orders(workspace_id,id)
);

CREATE TABLE deliveries (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id uuid NOT NULL REFERENCES workspaces(id),
  order_id uuid NOT NULL REFERENCES orders(id),
  license_id uuid REFERENCES licenses(id),
  status text NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','ready','downloaded','revoked')),
  media_asset_id uuid,
  package_hash char(64),
  expires_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  downloaded_at timestamptz,
  UNIQUE(workspace_id,id),
  FOREIGN KEY(workspace_id,order_id) REFERENCES orders(workspace_id,id),
  FOREIGN KEY(workspace_id,media_asset_id) REFERENCES media_assets(workspace_id,id),
  FOREIGN KEY(workspace_id,license_id) REFERENCES licenses(buyer_workspace_id,id)
);

CREATE TABLE revenue_splits (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id uuid NOT NULL REFERENCES workspaces(id),
  subject_type text NOT NULL CHECK(subject_type IN ('asset','license','brand_submission')),
  subject_id text NOT NULL,
  beneficiary_type text NOT NULL CHECK(beneficiary_type IN ('workspace','user','platform')),
  beneficiary_id text NOT NULL,
  basis_points integer NOT NULL CHECK(basis_points BETWEEN 0 AND 10000),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(workspace_id,subject_type,subject_id,beneficiary_type,beneficiary_id)
);

CREATE TABLE payouts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id uuid NOT NULL REFERENCES workspaces(id),
  operation_key text NOT NULL,
  beneficiary_type text NOT NULL,
  beneficiary_id text NOT NULL,
  currency char(3) NOT NULL,
  amount bigint NOT NULL CHECK(amount>0),
  status text NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','processing','paid','failed','reversed')),
  reference_type text NOT NULL,
  reference_id text NOT NULL,
  provider_payout_id text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(workspace_id,operation_key),
  UNIQUE(workspace_id,id)
);

CREATE TABLE brand_briefs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id uuid NOT NULL REFERENCES workspaces(id),
  title text NOT NULL,
  description text NOT NULL,
  budget_amount bigint NOT NULL CHECK(budget_amount>=0),
  currency char(3) NOT NULL DEFAULT 'USD',
  deadline timestamptz,
  requirements jsonb NOT NULL DEFAULT '{}'::jsonb,
  status text NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','open','review','awarded','closed','cancelled')),
  created_by uuid NOT NULL REFERENCES users(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(workspace_id,id)
);

CREATE TABLE brand_submissions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  brief_id uuid NOT NULL REFERENCES brand_briefs(id),
  submitting_workspace_id uuid NOT NULL REFERENCES workspaces(id),
  asset_snapshot_id uuid NOT NULL REFERENCES asset_snapshots(id),
  rights_manifest_id uuid NOT NULL REFERENCES rights_manifests(id),
  status text NOT NULL DEFAULT 'submitted' CHECK(status IN ('submitted','shortlisted','rejected','awarded','withdrawn')),
  notes text NOT NULL DEFAULT '',
  submitted_by uuid NOT NULL REFERENCES users(id),
  submitted_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(brief_id,submitting_workspace_id,asset_snapshot_id),
  UNIQUE(submitting_workspace_id,id),
  FOREIGN KEY(submitting_workspace_id,asset_snapshot_id) REFERENCES asset_snapshots(workspace_id,id),
  FOREIGN KEY(submitting_workspace_id,rights_manifest_id,asset_snapshot_id) REFERENCES rights_manifests(workspace_id,id,asset_snapshot_id)
);

CREATE TABLE support_tickets (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id uuid NOT NULL REFERENCES workspaces(id),
  opened_by uuid NOT NULL REFERENCES users(id),
  category text NOT NULL CHECK(category IN ('generation','billing','rights','account','marketplace','other')),
  priority text NOT NULL DEFAULT 'normal' CHECK(priority IN ('low','normal','high','urgent')),
  status text NOT NULL DEFAULT 'open' CHECK(status IN ('open','waiting_customer','waiting_internal','resolved','closed')),
  subject text NOT NULL,
  description text NOT NULL,
  related_type text,
  related_id text,
  assigned_to uuid REFERENCES users(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(workspace_id,id)
);

CREATE TABLE release_evidence (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  gate text NOT NULL,
  evidence_key text NOT NULL,
  status text NOT NULL CHECK(status IN ('missing','in_progress','passed','waived','failed')),
  evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
  owner text NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(gate,evidence_key)
);

CREATE INDEX ix_orders_status_created ON orders(workspace_id,status,created_at DESC);
CREATE INDEX ix_payments_status_created ON payments(workspace_id,status,created_at DESC);
CREATE INDEX ix_refunds_status_created ON refunds(workspace_id,status,created_at DESC);
CREATE INDEX ix_offers_status_created ON asset_offers(workspace_id,status,created_at DESC);
CREATE INDEX ix_moderation_queue ON moderation_cases(workspace_id,status,severity,created_at);
CREATE INDEX ix_support_queue ON support_tickets(workspace_id,status,priority,created_at);
CREATE INDEX ix_brand_briefs_status_deadline ON brand_briefs(status,deadline);

-- Immutable commercial records.
CREATE TRIGGER immutable_ai_runs BEFORE UPDATE OR DELETE ON ai_runs FOR EACH ROW EXECUTE FUNCTION prevent_immutable_mutation();
CREATE TRIGGER immutable_product_events BEFORE UPDATE OR DELETE ON product_events FOR EACH ROW EXECUTE FUNCTION prevent_immutable_mutation();
CREATE TRIGGER immutable_rights_evidence_delete BEFORE DELETE ON rights_evidence FOR EACH ROW EXECUTE FUNCTION prevent_immutable_mutation();
CREATE TRIGGER immutable_licenses_delete BEFORE DELETE ON licenses FOR EACH ROW EXECUTE FUNCTION prevent_immutable_mutation();
CREATE TRIGGER immutable_revenue_splits BEFORE UPDATE OR DELETE ON revenue_splits FOR EACH ROW EXECUTE FUNCTION prevent_immutable_mutation();

CREATE OR REPLACE FUNCTION check_revenue_split_total() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE target_ws uuid; target_type text; target_id text; total_bp integer;
BEGIN
  target_ws:=COALESCE(NEW.workspace_id,OLD.workspace_id);
  target_type:=COALESCE(NEW.subject_type,OLD.subject_type);
  target_id:=COALESCE(NEW.subject_id,OLD.subject_id);
  SELECT COALESCE(sum(basis_points),0) INTO total_bp FROM revenue_splits
    WHERE workspace_id=target_ws AND subject_type=target_type AND subject_id=target_id;
  IF total_bp<>10000 THEN RAISE EXCEPTION 'revenue split total must equal 10000 basis points, got %',total_bp; END IF;
  RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER revenue_split_total_must_balance
AFTER INSERT OR UPDATE OR DELETE ON revenue_splits DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION check_revenue_split_total();

-- Tenant isolation for workspace-owned tables.
DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY[
    'project_branches','project_comments','user_preferences','product_events','ai_runs','moderation_cases',
    'rights_evidence','orders','order_items','payments','refunds','subscriptions','asset_offers','deliveries',
    'revenue_splits','payouts','brand_briefs','support_tickets'
  ] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',t);
    EXECUTE format('CREATE POLICY tenant_policy ON %I USING (workspace_id::text=current_setting(''app.workspace_id'',true)) WITH CHECK (workspace_id::text=current_setting(''app.workspace_id'',true))',t);
  END LOOP;
END $$;

ALTER TABLE brand_submissions ENABLE ROW LEVEL SECURITY;
ALTER TABLE brand_submissions FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_policy ON brand_submissions
USING (submitting_workspace_id::text=current_setting('app.workspace_id',true))
WITH CHECK (submitting_workspace_id::text=current_setting('app.workspace_id',true));

ALTER TABLE offer_reservations ENABLE ROW LEVEL SECURITY;
ALTER TABLE offer_reservations FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_policy ON offer_reservations
USING (buyer_workspace_id::text=current_setting('app.workspace_id',true))
WITH CHECK (buyer_workspace_id::text=current_setting('app.workspace_id',true));

-- Versioned AI facts are data, not hidden prompt strings in application code.
WITH registry(kind,key,version,status,definition,created_by) AS (
  VALUES
  ('prompt','song-patch','1.0.0','active','{"purpose":"Propose bounded SongSpec JSON Patch operations","forbidden":["billing","provider_submit","master_selection","rights_grant"],"locked_paths":"must_preserve"}'::jsonb,'migration'),
  ('model','deepseek-v4-flash','1.0.0','active','{"provider":"deepseek","fallback":"deterministic-local","use":"structured SongSpec patch proposals"}'::jsonb,'migration'),
  ('tool_schema','song-patch-command','1.0.0','active','{"required":["command","base_revision","operations","preserve","reason"]}'::jsonb,'migration'),
  ('quality_ruleset','quality-engine','2.1-final','active','{"dimensions":28,"languages":["zh-CN","yue"],"variables":["PAC","TSMI","NSRQ","C3AC"],"loop":"bounded"}'::jsonb,'migration')
)
INSERT INTO ai_registries(kind,key,version,status,definition,checksum,created_by)
SELECT kind,key,version,status,definition,encode(digest(definition::text,'sha256'),'hex'),created_by FROM registry
ON CONFLICT(kind,key,version) DO NOTHING;

-- Seed a commercially conservative catalog and a development license template.
INSERT INTO product_catalog(sku,product_type,name,description,currency,unit_amount,credits,metadata) VALUES
('CREDITS_100','credit_pack','100 Credits','用于生成候选音乐的额度包','USD',1200,100,'{"recommended":true}'::jsonb),
('CREDITS_500','credit_pack','500 Credits','团队创作额度包','USD',5000,500,'{"savings_percent":16}'::jsonb),
('CREATOR_MONTHLY','subscription','Creator Monthly','创作者工作室月度订阅','USD',1900,200,'{"period":"month"}'::jsonb),
('TEAM_MONTHLY','subscription','Team Monthly','团队协作、审批和扩展存储','USD',5900,600,'{"period":"month"}'::jsonb)
ON CONFLICT(sku) DO NOTHING;

INSERT INTO license_templates(id,workspace_id,name,version,status,terms,terms_hash,created_by) VALUES
('12121212-1212-1212-1212-121212121212',NULL,'Standard Commercial License',1,'active',
 '{"capabilities":["commercial_use","download"],"exclusive":false,"attribution":"optional","modification":"allowed","sublicense":"blocked","content_id":"blocked","disclaimer":"Rights are limited by the bound Rights Manifest."}'::jsonb,
 encode(digest('{"capabilities":["commercial_use","download"],"exclusive":false,"attribution":"optional","modification":"allowed","sublicense":"blocked","content_id":"blocked","disclaimer":"Rights are limited by the bound Rights Manifest."}', 'sha256'),'hex'),'migration')
ON CONFLICT DO NOTHING;

INSERT INTO system_settings(key,value) VALUES
('payments_enabled','true'::jsonb),('marketplace_enabled','true'::jsonb),('licenses_enabled','true'::jsonb),
('public_sharing_enabled','false'::jsonb),('payouts_enabled','false'::jsonb),('brand_market_enabled','true'::jsonb)
ON CONFLICT DO NOTHING;

INSERT INTO release_evidence(gate,evidence_key,status,evidence,owner) VALUES
('G9','compose_e2e','in_progress','{}','engineering'),
('G9','provider_contract','missing','{"note":"Requires approved provider credentials and contract"}','legal'),
('G10','payment_reconciliation','in_progress','{}','finance'),
('G10','backup_restore','in_progress','{}','sre'),
('G11','creator_retention','missing','{}','product'),
('G12','commercial_license_pilot','missing','{}','business')
ON CONFLICT DO NOTHING;

-- Grants for API. Worker does not need market write access.
GRANT SELECT,INSERT,UPDATE ON project_branches,project_comments,user_preferences,moderation_cases,rights_evidence,
  orders,order_items,payments,refunds,subscriptions,asset_offers,deliveries,revenue_splits,payouts,brand_briefs,
  brand_submissions,support_tickets,offer_reservations TO music_app;
GRANT SELECT,INSERT ON product_events,ai_runs TO music_app;
GRANT SELECT,INSERT,UPDATE ON payment_inbox TO music_app;
GRANT SELECT ON product_catalog,license_templates,ai_registries TO music_app;
GRANT SELECT,INSERT,UPDATE ON release_evidence TO music_app;
GRANT SELECT,INSERT,UPDATE ON licenses TO music_app;
GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO music_app,music_worker;

-- Cross-workspace discovery exposes only intentionally public market fields.
CREATE VIEW marketplace_offers_public AS
SELECT o.id,o.workspace_id AS seller_workspace_id,w.name AS seller_name,o.asset_snapshot_id,o.rights_manifest_id,
       o.license_template_id,o.title,o.description,o.price_amount,o.currency,o.territory,o.duration_days,o.exclusive,
       o.status,o.created_at,a.media_hash,a.snapshot->>'title' AS asset_title,r.manifest AS rights_manifest,r.version AS rights_version
FROM asset_offers o
JOIN workspaces w ON w.id=o.workspace_id
JOIN asset_snapshots a ON a.id=o.asset_snapshot_id
JOIN rights_manifests r ON r.id=o.rights_manifest_id
WHERE o.status='active'
  AND r.version=(SELECT max(r2.version) FROM rights_manifests r2 WHERE r2.asset_snapshot_id=o.asset_snapshot_id)
  AND COALESCE((r.manifest->>'legal_hold')::boolean,false)=false
  AND COALESCE(r.manifest->'capabilities'->'commercial_use'->>'status','unknown')='allowed'
  AND COALESCE(r.manifest->'capabilities'->'license'->>'status','unknown')='allowed'
  AND NOT EXISTS (
    SELECT 1 FROM moderation_cases c
    WHERE c.workspace_id=o.workspace_id AND c.subject_type='asset' AND c.subject_id=o.asset_snapshot_id::text
      AND (c.legal_hold OR c.status IN ('open','triage','restricted','appealed'))
  )
  AND (NOT o.exclusive OR NOT EXISTS (
    SELECT 1 FROM offer_reservations rs
    WHERE rs.offer_id=o.id AND rs.status IN ('pending','confirmed') AND (rs.status='confirmed' OR rs.expires_at>now())
  ));

CREATE VIEW brand_briefs_public AS
SELECT b.id,b.workspace_id AS buyer_workspace_id,w.name AS buyer_name,b.title,b.description,b.budget_amount,b.currency,
       b.deadline,b.requirements,b.status,b.created_at
FROM brand_briefs b JOIN workspaces w ON w.id=b.workspace_id
WHERE b.status='open';

ALTER TABLE licenses ENABLE ROW LEVEL SECURITY;
ALTER TABLE licenses FORCE ROW LEVEL SECURITY;
CREATE POLICY party_policy ON licenses
USING (seller_workspace_id::text=current_setting('app.workspace_id',true) OR buyer_workspace_id::text=current_setting('app.workspace_id',true))
WITH CHECK (seller_workspace_id::text=current_setting('app.workspace_id',true) OR buyer_workspace_id::text=current_setting('app.workspace_id',true));

CREATE OR REPLACE FUNCTION review_brand_submissions(target_brief uuid)
RETURNS TABLE(id uuid,brief_id uuid,submitting_workspace_id uuid,submitter_name text,asset_snapshot_id uuid,rights_manifest_id uuid,status text,notes text,submitted_by uuid,submitted_at timestamptz,updated_at timestamptz)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=public AS $$
DECLARE owner_ws uuid; request_ws text;
BEGIN
  request_ws:=current_setting('app.workspace_id',true);
  SELECT workspace_id INTO owner_ws FROM brand_briefs WHERE brand_briefs.id=target_brief;
  IF owner_ws IS NULL OR owner_ws::text<>request_ws THEN RAISE EXCEPTION 'brief owner required'; END IF;
  RETURN QUERY SELECT s.id,s.brief_id,s.submitting_workspace_id,w.name,s.asset_snapshot_id,s.rights_manifest_id,s.status,s.notes,s.submitted_by,s.submitted_at,s.updated_at
    FROM brand_submissions s JOIN workspaces w ON w.id=s.submitting_workspace_id WHERE s.brief_id=target_brief ORDER BY s.submitted_at;
END $$;

GRANT SELECT ON marketplace_offers_public,brand_briefs_public TO music_app;
GRANT EXECUTE ON FUNCTION review_brand_submissions(uuid) TO music_app;

CREATE OR REPLACE FUNCTION record_license_revenue(target_license uuid)
RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=public AS $$
DECLARE lic licenses; ord orders; payout_id uuid; seller_amount bigint;
BEGIN
  SELECT * INTO lic FROM licenses WHERE id=target_license;
  IF lic.id IS NULL OR lic.status<>'active' THEN RAISE EXCEPTION 'active license required'; END IF;
  SELECT * INTO ord FROM orders WHERE id=lic.order_id;
  IF ord.id IS NULL OR ord.status NOT IN ('paid','fulfilled') THEN RAISE EXCEPTION 'paid order required'; END IF;
  seller_amount:=floor(ord.total*0.85);
  INSERT INTO revenue_splits(workspace_id,subject_type,subject_id,beneficiary_type,beneficiary_id,basis_points) VALUES
    (lic.seller_workspace_id,'license',lic.id::text,'workspace',lic.seller_workspace_id::text,8500),
    (lic.seller_workspace_id,'license',lic.id::text,'platform','platform',1500)
  ON CONFLICT DO NOTHING;
  SELECT id INTO payout_id FROM payouts WHERE workspace_id=lic.seller_workspace_id AND operation_key='license:'||lic.id::text||':seller-payout';
  IF payout_id IS NULL AND seller_amount>0 THEN
    payout_id:=gen_random_uuid();
    INSERT INTO payouts(id,workspace_id,operation_key,beneficiary_type,beneficiary_id,currency,amount,status,reference_type,reference_id)
    VALUES(payout_id,lic.seller_workspace_id,'license:'||lic.id::text||':seller-payout','workspace',lic.seller_workspace_id::text,ord.currency,seller_amount,'pending','license',lic.id::text);
  END IF;
  RETURN payout_id;
END $$;
GRANT EXECUTE ON FUNCTION record_license_revenue(uuid) TO music_app;

CREATE OR REPLACE FUNCTION reverse_license_revenue(target_order uuid)
RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=public AS $$
DECLARE lic_id uuid;
BEGIN
  SELECT id INTO lic_id FROM licenses WHERE order_id=target_order;
  IF lic_id IS NOT NULL THEN
    UPDATE payouts SET status='reversed',updated_at=now() WHERE reference_type='license' AND reference_id=lic_id::text AND status IN ('pending','processing','paid','failed');
  END IF;
END $$;
GRANT EXECUTE ON FUNCTION reverse_license_revenue(uuid) TO music_app;

CREATE OR REPLACE FUNCTION platform_payout_queue()
RETURNS TABLE(id uuid,workspace_id uuid,workspace_name text,operation_key text,beneficiary_type text,beneficiary_id text,currency char(3),amount bigint,status text,reference_type text,reference_id text,provider_payout_id text,created_at timestamptz,updated_at timestamptz)
LANGUAGE sql SECURITY DEFINER SET search_path=public AS $$
  SELECT p.id,p.workspace_id,w.name,p.operation_key,p.beneficiary_type,p.beneficiary_id,p.currency,p.amount,p.status,p.reference_type,p.reference_id,p.provider_payout_id,p.created_at,p.updated_at
  FROM payouts p JOIN workspaces w ON w.id=p.workspace_id ORDER BY p.created_at DESC
$$;
GRANT EXECUTE ON FUNCTION platform_payout_queue() TO music_app;

CREATE OR REPLACE FUNCTION set_platform_payout_status(target_payout uuid,new_status text,new_provider_id text)
RETURNS payouts
LANGUAGE plpgsql SECURITY DEFINER SET search_path=public AS $$
DECLARE result payouts;
BEGIN
  IF new_status NOT IN ('pending','processing','paid','failed','reversed') THEN RAISE EXCEPTION 'invalid payout status'; END IF;
  UPDATE payouts SET status=new_status,provider_payout_id=COALESCE(new_provider_id,provider_payout_id),updated_at=now() WHERE id=target_payout RETURNING * INTO result;
  IF result.id IS NULL THEN RAISE EXCEPTION 'payout not found'; END IF;
  RETURN result;
END $$;
GRANT EXECUTE ON FUNCTION set_platform_payout_status(uuid,text,text) TO music_app;

CREATE OR REPLACE FUNCTION reserve_marketplace_offer(target_offer uuid,target_order uuid,target_reservation uuid)
RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=public AS $$
DECLARE request_ws text; offer_row asset_offers; latest_manifest rights_manifests;
BEGIN
  request_ws:=current_setting('app.workspace_id',true);
  SELECT * INTO offer_row FROM asset_offers WHERE id=target_offer FOR UPDATE;
  IF offer_row.id IS NULL OR offer_row.status<>'active' THEN RAISE EXCEPTION 'active offer not found'; END IF;
  IF offer_row.workspace_id::text=request_ws THEN RAISE EXCEPTION 'seller cannot purchase own offer'; END IF;
  SELECT * INTO latest_manifest FROM rights_manifests WHERE asset_snapshot_id=offer_row.asset_snapshot_id ORDER BY version DESC LIMIT 1;
  IF latest_manifest.id IS NULL OR latest_manifest.id<>offer_row.rights_manifest_id THEN RAISE EXCEPTION 'offer rights manifest is stale'; END IF;
  IF COALESCE((latest_manifest.manifest->>'legal_hold')::boolean,false)
     OR COALESCE(latest_manifest.manifest->'capabilities'->'commercial_use'->>'status','unknown')<>'allowed'
     OR COALESCE(latest_manifest.manifest->'capabilities'->'license'->>'status','unknown')<>'allowed' THEN
    RAISE EXCEPTION 'offer rights are not licensable';
  END IF;
  IF EXISTS(SELECT 1 FROM moderation_cases c WHERE c.workspace_id=offer_row.workspace_id AND c.subject_type='asset' AND c.subject_id=offer_row.asset_snapshot_id::text AND (c.legal_hold OR c.status IN ('open','triage','restricted','appealed'))) THEN
    RAISE EXCEPTION 'asset is restricted';
  END IF;
  UPDATE offer_reservations SET status='expired',updated_at=now() WHERE offer_id=target_offer AND status='pending' AND expires_at<=now();
  IF offer_row.exclusive AND EXISTS(SELECT 1 FROM offer_reservations WHERE offer_id=target_offer AND status IN ('pending','confirmed')) THEN
    RAISE EXCEPTION 'exclusive offer is reserved or sold';
  END IF;
  INSERT INTO offer_reservations(id,offer_id,buyer_workspace_id,order_id,status,expires_at)
  VALUES(target_reservation,target_offer,request_ws::uuid,target_order,'pending',now()+interval '30 minutes');
  RETURN target_reservation;
END $$;
GRANT EXECUTE ON FUNCTION reserve_marketplace_offer(uuid,uuid,uuid) TO music_app;

CREATE OR REPLACE FUNCTION licensed_delivery_package(target_delivery uuid)
RETURNS TABLE(delivery_id uuid,delivery_status text,expires_at timestamptz,package_payload jsonb,bucket text,object_key text,mime_type text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=public AS $$
DECLARE request_ws text;
BEGIN
  request_ws:=current_setting('app.workspace_id',true);
  RETURN QUERY
  SELECT d.id,d.status,d.expires_at,
    jsonb_build_object(
      'license',jsonb_build_object(
        'id',l.id,'status',l.status,'licensee_name',l.licensee_name,'territory',l.territory,
        'starts_at',l.starts_at,'ends_at',l.ends_at,'terms',l.terms_snapshot,'license_hash',l.license_hash,
        'seller_workspace_id',l.seller_workspace_id,'buyer_workspace_id',l.buyer_workspace_id,'order_id',l.order_id
      ),
      'asset_snapshot',a.snapshot,
      'rights_manifest',r.manifest
    ),m.bucket,m.object_key,m.mime_type
  FROM deliveries d
  JOIN licenses l ON l.id=d.license_id
  JOIN asset_snapshots a ON a.id=l.asset_snapshot_id
  JOIN rights_manifests r ON r.id=l.rights_manifest_id
  JOIN audio_candidates c ON c.id=a.candidate_id
  JOIN media_assets m ON m.id=c.media_asset_id
  WHERE d.id=target_delivery AND d.workspace_id::text=request_ws AND l.buyer_workspace_id::text=request_ws
    AND l.status='active' AND d.status IN ('ready','downloaded');
END $$;
GRANT EXECUTE ON FUNCTION licensed_delivery_package(uuid) TO music_app;

CREATE OR REPLACE FUNCTION set_brand_submission_status(target_submission uuid,new_status text)
RETURNS brand_submissions
LANGUAGE plpgsql SECURITY DEFINER SET search_path=public AS $$
DECLARE owner_ws uuid; request_ws text; result brand_submissions;
BEGIN
  IF new_status NOT IN ('submitted','shortlisted','rejected','awarded','withdrawn') THEN RAISE EXCEPTION 'invalid status'; END IF;
  request_ws:=current_setting('app.workspace_id',true);
  SELECT b.workspace_id INTO owner_ws FROM brand_submissions s JOIN brand_briefs b ON b.id=s.brief_id WHERE s.id=target_submission;
  IF owner_ws IS NULL OR owner_ws::text<>request_ws THEN RAISE EXCEPTION 'brief owner required'; END IF;
  UPDATE brand_submissions SET status=new_status,updated_at=now() WHERE id=target_submission RETURNING * INTO result;
  RETURN result;
END $$;
GRANT EXECUTE ON FUNCTION set_brand_submission_status(uuid,text) TO music_app;
