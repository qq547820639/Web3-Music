-- Right to erasure did not erase the second factor.
--
-- 014's erase_user_identity replaces identity columns on `users` and clears sessions,
-- preferences and memberships. 015 then added six authentication columns to the same table, and
-- none of them were in that UPDATE -- so a data subject who armed a TOTP and then exercised
-- Article 17 kept, in the erased row: a seal-openable authenticator seed, a set of HMACs over
-- single-use codes, the step counter, and the timestamps of both. The drill that certified
-- erasure (scripts/erasure_drill.py, 34 checks) could not have caught it: it was written before
-- 015 existed, and its coverage assertion enumerates *keys referencing users*, not the columns
-- users itself gained. This migration closes the defect; the companion change to the drill makes
-- the next such addition fail a check instead of waiting for a reader.
--
-- What is deliberately kept: auth_sessions.amr and .mfa_at. Those record *when* a factor was
-- presented and by which method, i.e. session activity, and they sit in the same row as
-- created_at/revoked_at, which the retention policy already keeps for every erased account
-- (014's summary.retained, and the "credential lifecycle events are retained" line in
-- docs/RELEASE_CHECKLIST.md). They carry no factor material: the seed and the recovery hashes
-- are below, and both are destroyed.
--
-- Every other statement is byte-identical to 014; the change is the column list of the final
-- UPDATE and one line of the returned summary.
CREATE OR REPLACE FUNCTION erase_user_identity(target_user uuid, confirmation text)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=public AS $$
DECLARE
  account record;
  orphan_workspace text;
  sessions_revoked integer := 0;
  preferences_removed integer := 0;
  memberships_removed integer := 0;
  second_factor_removed boolean := false;
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

  UPDATE auth_sessions SET revoked_at=now(), revoke_reason='account erased'
    WHERE user_id=target_user AND revoked_at IS NULL;
  GET DIAGNOSTICS sessions_revoked = ROW_COUNT;

  DELETE FROM user_preferences WHERE user_id=target_user;
  GET DIAGNOSTICS preferences_removed = ROW_COUNT;

  DELETE FROM workspace_members WHERE user_id=target_user;
  GET DIAGNOSTICS memberships_removed = ROW_COUNT;

  -- Reported back so the caller (and the drill) can tell "nothing to erase" from "an armed
  -- second factor was destroyed"; a boolean, because the counts are the secret.
  second_factor_removed := (account.mfa_enrolled_at IS NOT NULL OR account.mfa_secret_enc IS NOT NULL);

  UPDATE users
     SET email='erased-' || target_user::text || '@invalid.invalid',
         display_name='已删除用户',
         -- Not a parseable pbkdf2 encoding, so auth.py's verify_password returns False for
         -- every input; the login path is closed without needing to keep the old hash.
         password_hash='erased$0$0$0',
         status='erased',
         is_platform_admin=false,
         erased_at=now(),
         -- 015's authentication material goes with the identity it protected. mfa_enrolled_at
         -- also has to be NULL, because get_user's second-factor gate reads it: leaving it set on
         -- an erased account would hold "user unavailable" hostage to a code nobody can produce.
         mfa_secret_enc=NULL,
         mfa_secret_issued_at=NULL,
         mfa_enrolled_at=NULL,
         mfa_last_step=NULL,
         mfa_recovery=NULL,
         mfa_recovery_generated_at=NULL
   WHERE id=target_user;

  summary := jsonb_build_object(
    'already_erased', false,
    'user_id', target_user::text,
    'sessions_revoked', sessions_revoked,
    'preferences_removed', preferences_removed,
    'memberships_removed', memberships_removed,
    'second_factor_removed', second_factor_removed,
    'retained', jsonb_build_object(
      'reason', 'financial and provenance records keep a pseudonymous actor id; users.email/display_name are now non-identifying; auth_sessions.amr/mfa_at are retained as session activity alongside created_at/revoked_at',
      'tables', jsonb_build_array('ledger_transactions','payouts','revenue_splits','orders','asset_snapshots','generation_jobs','song_projects','audit_events'))
  );
  RETURN summary;
END $$;

REVOKE ALL ON FUNCTION erase_user_identity(uuid, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION erase_user_identity(uuid, text) TO music_app;
