-- Third demo tenant for the cross-tenant isolation acceptance suite (release gate G9-5).
-- licenses/deliveries/payouts are deliberately readable by the seller OR the buyer
-- (see 002 licenses RLS), so a two-workspace fixture cannot prove that clause is bounded:
-- with only A and B, "B can see it" is both the expected result and the leak symptom.
INSERT INTO workspaces(id,name,plan) VALUES
('cccccccc-cccc-cccc-cccc-cccccccccccc','Isolation Third Workspace','trial')
ON CONFLICT DO NOTHING;

-- Reuses the seeded demo-viewer pbkdf2 hash, so the password is: demo-viewer
INSERT INTO users(id,email,display_name,password_hash,is_platform_admin) VALUES
('dddddddd-dddd-dddd-dddd-dddddddddddd','third@example.local','Isolation Third Owner','pbkdf2_sha256$200000$ZqW4CkyAIk65Cg5ebbDXyw==$A/i3PK8dd22PrI7wQG43zMILS0c9J+7nhVnCkMEKqW4=',false)
ON CONFLICT DO NOTHING;

INSERT INTO workspace_members(workspace_id,user_id,role) VALUES
('cccccccc-cccc-cccc-cccc-cccccccccccc','dddddddd-dddd-dddd-dddd-dddddddddddd','owner')
ON CONFLICT DO NOTHING;

-- 001 creates accounts and the opening balance via loops over workspaces that ran
-- before this row existed, so both halves have to be replayed here.
INSERT INTO ledger_accounts(workspace_id,account_type)
SELECT 'cccccccc-cccc-cccc-cccc-cccccccccccc',t.account_type
FROM (VALUES('available'),('held'),('expense'),('refunds'),('issued')) t(account_type)
ON CONFLICT DO NOTHING;

DO $$ DECLARE tx uuid; avail uuid; issued uuid; BEGIN
  IF NOT EXISTS (SELECT 1 FROM ledger_transactions WHERE workspace_id='cccccccc-cccc-cccc-cccc-cccccccccccc' AND operation_key='seed:initial') THEN
    tx:=gen_random_uuid();
    SELECT id INTO avail FROM ledger_accounts WHERE workspace_id='cccccccc-cccc-cccc-cccc-cccccccccccc' AND account_type='available';
    SELECT id INTO issued FROM ledger_accounts WHERE workspace_id='cccccccc-cccc-cccc-cccc-cccccccccccc' AND account_type='issued';
    INSERT INTO ledger_transactions(id,workspace_id,operation_key,transaction_type,reference_type,reference_id,created_by)
    VALUES(tx,'cccccccc-cccc-cccc-cccc-cccccccccccc','seed:initial','seed','workspace','cccccccc-cccc-cccc-cccc-cccccccccccc','migration');
    INSERT INTO ledger_entries(workspace_id,transaction_id,account_id,delta) VALUES
    ('cccccccc-cccc-cccc-cccc-cccccccccccc',tx,avail,1000),('cccccccc-cccc-cccc-cccc-cccccccccccc',tx,issued,-1000);
  END IF;
END $$;
