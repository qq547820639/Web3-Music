-- Second factor (TOTP) for human accounts: enrolment, confirmation, revocation, recovery
-- codes, and the per-session proof that a second factor was actually presented.
--
-- Why another SECURITY DEFINER function set rather than application UPDATEs: measured against
-- the live stack, music_app holds SELECT only on `users` (db/migrations/001_production_candidate.sql:531),
-- so it cannot write these columns at all, and widening that grant would let every code path in
-- the API rewrite any account's authentication state. The functions below take the actor's own
-- user id from the authenticated identity the endpoint already resolved, and nothing else.
--
-- Why the session marker is a NEW column: 005_final_release.sql:44-54 (guard_auth_session_update)
-- raises if id, user_id, refresh_token_hash, csrf_token_hash, created_at or expires_at change,
-- which is the trigger that made the first erasure implementation fail (013 -> 014). `amr` and
-- `mfa_at` are not in that list, so writing them is exactly the "explicit revocation/last-seen
-- updates" latitude the 005 comment allows.
--
-- Enrolment is staged, never implicit:
--   * begin: a fresh secret is generated in-database and sealed with the server key; the
--     account is NOT armed, so a half-finished enrolment cannot lock anyone out;
--   * confirm: the caller proves knowledge of a code for the pending secret, which arms it and
--     returns the one-time recovery codes;
--   * a pending secret older than the confirmation window is void, so an abandoned enrolment
--     cannot be confirmed much later;
--   * disable requires a currently valid code (verified in the API before this function runs)
--     and clears everything, including the recovery set.

ALTER TABLE users ADD COLUMN IF NOT EXISTS mfa_secret_enc text;
ALTER TABLE users ADD COLUMN IF NOT EXISTS mfa_secret_issued_at timestamptz;
ALTER TABLE users ADD COLUMN IF NOT EXISTS mfa_enrolled_at timestamptz;
ALTER TABLE users ADD COLUMN IF NOT EXISTS mfa_last_step bigint;
ALTER TABLE users ADD COLUMN IF NOT EXISTS mfa_recovery jsonb;
ALTER TABLE users ADD COLUMN IF NOT EXISTS mfa_recovery_generated_at timestamptz;

ALTER TABLE auth_sessions ADD COLUMN IF NOT EXISTS amr text;
ALTER TABLE auth_sessions ADD COLUMN IF NOT EXISTS mfa_at timestamptz;

CREATE OR REPLACE FUNCTION begin_mfa_enrolment(target_user uuid, sealed_secret text)
RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=public AS $$
DECLARE account_status text;
BEGIN
  SELECT status INTO account_status FROM users WHERE id=target_user FOR UPDATE;
  IF account_status IS NULL THEN
    RAISE EXCEPTION 'account not found';
  END IF;
  IF account_status <> 'active' THEN
    RAISE EXCEPTION 'only an active account can enrol a second factor';
  END IF;
  UPDATE users
     SET mfa_secret_enc=sealed_secret, mfa_secret_issued_at=now(),
         -- a re-enrolment drops the previous recovery set: those codes belonged to the secret
         -- being replaced, and keeping them would leave a live backdoor after rotation.
         mfa_recovery=NULL, mfa_recovery_generated_at=NULL, mfa_last_step=NULL
   WHERE id=target_user;
END $$;

CREATE OR REPLACE FUNCTION confirm_mfa_enrolment(target_user uuid, recovery_hashes jsonb, window_seconds integer)
RETURNS TABLE (already_armed boolean, armed_at timestamptz)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=public AS $$
DECLARE account record;
BEGIN
  SELECT * INTO account FROM users WHERE id=target_user FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'account not found';
  END IF;
  IF account.mfa_enrolled_at IS NOT NULL THEN
    RETURN QUERY SELECT true, account.mfa_enrolled_at;
    RETURN;
  END IF;
  IF account.mfa_secret_enc IS NULL THEN
    RAISE EXCEPTION 'no pending second-factor enrolment to confirm';
  END IF;
  IF account.mfa_secret_issued_at IS NULL
     OR account.mfa_secret_issued_at < now() - make_interval(secs => window_seconds) THEN
    RAISE EXCEPTION 'the pending enrolment has expired; start again';
  END IF;
  UPDATE users SET mfa_enrolled_at=now(), mfa_recovery=recovery_hashes,
                   mfa_recovery_generated_at=now()
    WHERE id=target_user;
  RETURN QUERY SELECT false, now();
END $$;

CREATE OR REPLACE FUNCTION disable_mfa(target_user uuid)
RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=public AS $$
DECLARE was_armed boolean;
BEGIN
  SELECT mfa_enrolled_at IS NOT NULL INTO was_armed FROM users WHERE id=target_user FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'account not found';
  END IF;
  UPDATE users SET mfa_secret_enc=NULL, mfa_secret_issued_at=NULL, mfa_enrolled_at=NULL,
                   mfa_last_step=NULL, mfa_recovery=NULL, mfa_recovery_generated_at=NULL
    WHERE id=target_user;
  RETURN coalesce(was_armed, false);
END $$;

-- Single use, and the caller cannot read the stored values back: this consumes a recovery code
-- by hash and reports only whether one matched, because the hashes are the only thing between a
-- database read and account takeover.
CREATE OR REPLACE FUNCTION consume_mfa_recovery_code(target_user uuid, code_hash text)
RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=public AS $$
DECLARE remaining jsonb;
BEGIN
  SELECT mfa_recovery INTO remaining FROM users WHERE id=target_user FOR UPDATE;
  IF remaining IS NULL OR NOT jsonb_typeof(remaining) = 'array' THEN
    RETURN false;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM jsonb_array_elements(remaining) e WHERE e->>'hash' = code_hash) THEN
    RETURN false;
  END IF;
  UPDATE users
     SET mfa_recovery = (SELECT jsonb_agg(e) FROM jsonb_array_elements(remaining) e
                          WHERE e->>'hash' <> code_hash)
   WHERE id=target_user;
  RETURN true;
END $$;

-- The replay guard lives in the database so it is per-account rather than per worker process:
-- a code may be accepted once, and only for a step strictly greater than the last accepted one.
-- valid_window>1 in the adapter would otherwise let a captured code be replayed for minutes.
CREATE OR REPLACE FUNCTION accept_mfa_step(target_user uuid, step bigint)
RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=public AS $$
DECLARE last_step bigint;
BEGIN
  SELECT mfa_last_step INTO last_step FROM users WHERE id=target_user FOR UPDATE;
  IF last_step IS NOT NULL AND step <= last_step THEN
    RETURN false;
  END IF;
  UPDATE users SET mfa_last_step=step WHERE id=target_user;
  RETURN true;
END $$;

REVOKE ALL ON FUNCTION begin_mfa_enrolment(uuid, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION confirm_mfa_enrolment(uuid, jsonb, integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION disable_mfa(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION consume_mfa_recovery_code(uuid, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION accept_mfa_step(uuid, bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION begin_mfa_enrolment(uuid, text) TO music_app;
GRANT EXECUTE ON FUNCTION confirm_mfa_enrolment(uuid, jsonb, integer) TO music_app;
GRANT EXECUTE ON FUNCTION disable_mfa(uuid) TO music_app;
GRANT EXECUTE ON FUNCTION consume_mfa_recovery_code(uuid, text) TO music_app;
GRANT EXECUTE ON FUNCTION accept_mfa_step(uuid, bigint) TO music_app;
