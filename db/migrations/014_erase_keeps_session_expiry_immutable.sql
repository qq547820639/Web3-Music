-- erase_user_identity (013) could not run: its session UPDATE tripped a pre-existing
-- immutability trigger, so every erasure failed with 409 "immutable auth session fields cannot
-- change" and the account was left untouched.
--
-- 005_final_release.sql:44-54 installs guard_auth_session_update, a BEFORE UPDATE trigger that
-- raises if id, user_id, refresh_token_hash, csrf_token_hash, created_at **or expires_at**
-- changes; the comment on 005:43 says the table is "append-preserved except explicit
-- revocation/last-seen updates". 013 tried to shorten the session lifetime by writing
-- expires_at=now() alongside the revocation, and expires_at is on the immutable list.
--
-- Dropping the expires_at write is the correct fix, not a workaround, because revocation is
-- already what the auth path enforces: auth.py:150 raises 401 on revoked_at IS NOT NULL for
-- bearer tokens, auth.py:123 does the same for refresh, and auth.py:178 for the CSRF read — and
-- the app's own two revocation sites (main.py:177 rotation, main.py:188 logout) write
-- revoked_at/revoke_reason and never touch expires_at. The retained expires_at only widens the
-- lifetime of a row that is already dead, and the audit trail keeps when it was revoked.
--
-- Guard order and every other statement are byte-identical to 013; this is a one-clause change.
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

  UPDATE auth_sessions SET revoked_at=now(), revoke_reason='account erased'
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
