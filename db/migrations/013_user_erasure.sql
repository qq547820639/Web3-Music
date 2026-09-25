-- Right to erasure for a user account: identity is anonymised, sessions and
-- personal-preference rows go away, and the accounting record stays.
--
-- Hard deletion is not available here, and deliberately so: song_projects, generation_jobs,
-- asset_snapshots, licence rows, tickets and more all carry NOT NULL "created_by" /
-- "submitted_by" foreign keys onto users, and ledger_transactions / payouts / revenue_splits
-- must survive any erasure because the platform has to keep its books. So the erasure that
-- GDPR-style "deletion" reduces to in practice is: make the natural person unidentifiable,
-- cut their access, and leave the pseudonymous transaction trail that law requires. That is
-- what this function does, and what /api/account/erasure exposes.
--
-- Why a SECURITY DEFINER function again (same shape as 011/012): users, workspace_members
-- and auth_sessions are readable only through RLS-scoped roles, and an erasure must reach
-- rows in every workspace the account belongs to in one statement. music_app may call it
-- only for the identity it already authenticated; the endpoint passes actor.user_id and
-- nothing else, and the confirmation must equal the stored email.
--
-- PostgreSQL Anonymizer (the mature tool in this space, checked this round: per-column
-- SECURITY LABEL masking rules, destructive static masking, dynamic masking by role, needs
-- the extension plus pg_read_all_data) masks columns by rule across a whole table, which is
-- the opposite axis from "erase one subject's rows and identity everywhere", and it covers
-- none of the export half. Its masking-rule-table idea is borrowed; the extension is not.
--
-- Guards, each of them reachable and each tested:
--   * confirmation must match the account email;
--   * a platform administrator cannot self-erase (they hold cross-tenant authority, so
--     de-authorising has to be another operator's action);
--   * the sole owner of any workspace is refused, because removing them would leave that
--     tenant with no one able to administer it;
--   * an already-erased account is a no-op, so a retried request cannot double-fire.
DO $$
DECLARE constraint_name text;
BEGIN
  SELECT con.conname INTO constraint_name
    FROM pg_constraint con
    WHERE con.conrelid='users'::regclass AND con.contype='c'
      AND pg_get_constraintdef(con.oid) ILIKE '%status%'
    LIMIT 1;
  IF constraint_name IS NOT NULL THEN
    EXECUTE format('ALTER TABLE users DROP CONSTRAINT %I', constraint_name);
  END IF;
END $$;

ALTER TABLE users ADD CONSTRAINT users_status_check CHECK (status IN ('active','disabled','erased'));
ALTER TABLE users ADD COLUMN IF NOT EXISTS erased_at timestamptz;

CREATE OR REPLACE FUNCTION erase_user_identity(target_user uuid, confirmation text)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=public AS $$
DECLARE
  account record;
  orphan_workspace text;
  sessions_revoked integer := 0;
  preferences_removed integer := 0;
  memberships_removed integer := 0;
  summary jsonb;
BEGIN
  SELECT * INTO account FROM users WHERE id=target_user FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'account not found';
  END IF;
  IF account.status='erased' THEN
    RETURN jsonb_build_object('already_erased', true, 'erased_at', account.erased_at);
  END IF;
  IF coalesce(confirmation,'') <> account.email THEN
    RAISE EXCEPTION 'confirmation does not match the account email';
  END IF;
  IF account.is_platform_admin THEN
    RAISE EXCEPTION 'platform administrators must be de-authorised by another operator before erasure';
  END IF;

  SELECT w.name INTO orphan_workspace
    FROM workspace_members mine
    JOIN workspaces w ON w.id=mine.workspace_id
    WHERE mine.user_id=target_user AND mine.role='owner'
      AND (SELECT count(*) FROM workspace_members others
             WHERE others.workspace_id=mine.workspace_id AND others.role='owner') = 1
    LIMIT 1;
  IF orphan_workspace IS NOT NULL THEN
    RAISE EXCEPTION 'account is the only owner of workspace "%"; transfer ownership first', orphan_workspace;
  END IF;

  UPDATE auth_sessions SET revoked_at=now(), revoke_reason='account erased', expires_at=now()
    WHERE user_id=target_user AND revoked_at IS NULL;
  GET DIAGNOSTICS sessions_revoked = ROW_COUNT;

  DELETE FROM user_preferences WHERE user_id=target_user;
  GET DIAGNOSTICS preferences_removed = ROW_COUNT;

  DELETE FROM workspace_members WHERE user_id=target_user;
  GET DIAGNOSTICS memberships_removed = ROW_COUNT;

  UPDATE users
     SET email='erased-' || target_user::text || '@invalid.invalid',
         display_name='已删除用户',
         -- Not a parseable pbkdf2 encoding, so auth.py's verify_password returns False for
         -- every input; the login path is closed without needing to keep the old hash.
         password_hash='erased$0$0$0',
         status='erased',
         is_platform_admin=false,
         erased_at=now()
   WHERE id=target_user;

  summary := jsonb_build_object(
    'already_erased', false,
    'user_id', target_user::text,
    'sessions_revoked', sessions_revoked,
    'preferences_removed', preferences_removed,
    'memberships_removed', memberships_removed,
    'retained', jsonb_build_object(
      'reason', 'financial and provenance records keep a pseudonymous actor id; users.email/display_name are now non-identifying',
      'tables', jsonb_build_array('ledger_transactions','payouts','revenue_splits','orders','asset_snapshots','generation_jobs','song_projects','audit_events'))
  );
  RETURN summary;
END $$;

REVOKE ALL ON FUNCTION erase_user_identity(uuid, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION erase_user_identity(uuid, text) TO music_app;
