-- v12 final hardening: cross-tenant foreign keys, global template visibility and payout transitions.

ALTER TABLE subscriptions
  ADD CONSTRAINT subscriptions_workspace_order_fk
  FOREIGN KEY(workspace_id,order_id) REFERENCES orders(workspace_id,id);

ALTER TABLE license_templates
  ADD CONSTRAINT license_templates_workspace_fk
  FOREIGN KEY(workspace_id) REFERENCES workspaces(id);

CREATE UNIQUE INDEX ux_license_templates_global_name_version
  ON license_templates(name,version) WHERE workspace_id IS NULL;

ALTER TABLE license_templates ENABLE ROW LEVEL SECURITY;
ALTER TABLE license_templates FORCE ROW LEVEL SECURITY;
CREATE POLICY readable_templates ON license_templates
  FOR SELECT
  USING (workspace_id IS NULL OR workspace_id::text=current_setting('app.workspace_id',true));

CREATE OR REPLACE FUNCTION enforce_payout_transition() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.id<>NEW.id OR OLD.workspace_id<>NEW.workspace_id OR OLD.operation_key<>NEW.operation_key
     OR OLD.beneficiary_type<>NEW.beneficiary_type OR OLD.beneficiary_id<>NEW.beneficiary_id
     OR OLD.currency<>NEW.currency OR OLD.amount<>NEW.amount OR OLD.reference_type<>NEW.reference_type
     OR OLD.reference_id<>NEW.reference_id OR OLD.created_at<>NEW.created_at THEN
    RAISE EXCEPTION 'payout economic fields are immutable';
  END IF;
  IF OLD.status=NEW.status THEN RETURN NEW; END IF;
  IF NOT (
    (OLD.status='pending' AND NEW.status IN ('processing','failed','reversed')) OR
    (OLD.status='processing' AND NEW.status IN ('paid','failed','reversed')) OR
    (OLD.status='failed' AND NEW.status IN ('pending','reversed')) OR
    (OLD.status='paid' AND NEW.status='reversed')
  ) THEN
    RAISE EXCEPTION 'invalid payout transition % -> %',OLD.status,NEW.status;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER payout_transition_guard BEFORE UPDATE ON payouts
FOR EACH ROW EXECUTE FUNCTION enforce_payout_transition();

CREATE OR REPLACE FUNCTION prevent_payout_delete() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'payout records are append-preserved and cannot be deleted'; END $$;
CREATE TRIGGER immutable_payout_delete BEFORE DELETE ON payouts
FOR EACH ROW EXECUTE FUNCTION prevent_payout_delete();

-- Direct tenant writes to payout state are removed. Platform operations use the
-- SECURITY DEFINER functions created in migration 002 and remain fully audited.
REVOKE UPDATE,DELETE ON payouts FROM music_app;
