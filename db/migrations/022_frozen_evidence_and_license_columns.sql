-- Close the narrower half of the retention promise: `rights_evidence` content and the terms of an issued
-- `licenses` row become unwritable, while the two status writes the platform actually performs stay legal.
--
-- Why now, and why this shape (all readings taken on this tree, 2026-09-28):
--   * docs/RIGHTS_POLICY.md:68 promises "Legal Hold 下不得删除 Asset、媒体、Rights、Audit、Payment 或
--     Ledger 记录" and :62 promises a License is frozen across 卖方/买方/Asset/Rights Manifest/Template/
--     Terms/Territory/期限/Licensee/Hash -- and that its status may still move to
--     suspended/revoked/refunded.
--   * What the tree actually had (db/migrations/002_creation_asset_market_os.sql:462-463) is two
--     DELETE-only triggers, `immutable_rights_evidence_delete` and `immutable_licenses_delete`, so the
--     *content* of an evidence row and the *terms* of a licence were writable: measured with
--     scripts/hold_drill.py, which previously found that the "保留所有证据" half rests on the global
--     append-only list and that this pair was outside it (recorded in RELEASE_CHECKLIST item 28 and
--     RIGHTS_POLICY's last paragraph of that section).
--   * Freezing UPDATE outright would have broken promised behaviour, and that is measurable, not a guess:
--     the product writes exactly two UPDATE shapes on these tables --
--     services/api/app/routers/assets.py:217 (`status`,`reviewed_by`,`reviewed_at` when a reviewer accepts
--     evidence) and services/api/app/routers/market.py:372 (`status='refunded'` on a refund) -- and the
--     refund one is exercised by the commercial acceptance flow. So the control has to be per column.
--
-- A tightening, not a relaxation: this adds a refusal that binds the application role (`music_app`) and
-- every other role except the documented disarm (`session_replication_role=replica`), which is the same
-- escape hatch the existing immutability triggers already honour. The frozen set is derived from the
-- policy sentence, so what stays writable is exactly what that sentence names as mutable: the review
-- metadata on evidence, and `status`/`activated_at` on a licence.

CREATE OR REPLACE FUNCTION frozen_columns_except() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  -- A trigger function may not declare parameters: the list arrives as the trigger's first argument,
  -- which is what `EXECUTE FUNCTION frozen_columns_except('{status,…}')` below passes. The first attempt
  -- at this file declared `frozen_columns_except(allowed text[])` and the migrate service refused to
  -- compile it: "the arguments of the trigger can be accessed through TG_NARGS and TG_ARGV instead".
  -- TG_ARGV is 0-based: reading TG_ARGV[1] yields NULL, and a NULL `allowed` made FOREACH raise
  -- "FOREACH expression must not be null" on *every* UPDATE -- including the two the platform performs.
  -- That is how it was caught, before any of it could be certified.
  allowed text[] := TG_ARGV[0];
  projected_new jsonb := to_jsonb(NEW);
  projected_old jsonb := to_jsonb(OLD);
  key text;
  moved text;
BEGIN
  IF allowed IS NULL THEN
    RAISE EXCEPTION 'frozen_columns_except is wired to % without a column list in EXECUTE FUNCTION',
      TG_TABLE_NAME;
  END IF;
  FOREACH key IN ARRAY allowed LOOP
    projected_new := projected_new - key;
    projected_old := projected_old - key;
  END LOOP;
  SELECT string_agg(k, ',' ORDER BY k) INTO moved
  FROM jsonb_object_keys(projected_new) k
  WHERE projected_new -> k IS DISTINCT FROM projected_old -> k;
  IF moved IS NOT NULL THEN
    RAISE EXCEPTION '% rows are frozen except for %; attempted change: %', TG_TABLE_NAME, allowed, moved;
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER immutable_rights_evidence_content BEFORE UPDATE ON rights_evidence
  FOR EACH ROW EXECUTE FUNCTION frozen_columns_except('{status,reviewed_by,reviewed_at}');

CREATE TRIGGER immutable_license_terms BEFORE UPDATE ON licenses
  FOR EACH ROW EXECUTE FUNCTION frozen_columns_except('{status,activated_at}');

-- The two writes the platform makes must still land; say so in the database itself so a later round cannot
-- read the freeze as "nothing may be touched".
COMMENT ON FUNCTION frozen_columns_except() IS
  'Trigger helper: refuse an UPDATE that changes any column outside the allowed list. Bound on '
  'rights_evidence (review metadata stays writable) and licenses (status and activated_at stay writable); '
  'the product''s own writes are services/api/app/routers/assets.py:217 and market.py:372.';
