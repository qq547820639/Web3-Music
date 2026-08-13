-- 009_prepare_brand_award_ambiguous_column.sql
-- prepare_brand_award's RETURNS TABLE declares an OUT column named
-- asset_snapshot_id, so the unqualified reference in the WHERE clause is
-- ambiguous between that OUT variable and rights_manifests.asset_snapshot_id,
-- raising "column reference asset_snapshot_id is ambiguous" when awarding a
-- brand submission. Qualify the table column reference.

CREATE OR REPLACE FUNCTION prepare_brand_award(target_submission uuid)
RETURNS TABLE(
  submission_id uuid,brief_id uuid,buyer_workspace_id uuid,seller_workspace_id uuid,
  asset_snapshot_id uuid,rights_manifest_id uuid,budget_amount bigint,currency char(3),
  brief_title text,requirements jsonb
)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=public AS $$
DECLARE request_ws text; sub brand_submissions; brief brand_briefs; latest rights_manifests;
BEGIN
  request_ws:=current_setting('app.workspace_id',true);
  SELECT * INTO sub FROM brand_submissions WHERE id=target_submission FOR UPDATE;
  IF sub.id IS NULL THEN RAISE EXCEPTION 'submission not found'; END IF;
  SELECT * INTO brief FROM brand_briefs WHERE id=sub.brief_id FOR UPDATE;
  IF brief.id IS NULL OR brief.workspace_id::text<>request_ws THEN RAISE EXCEPTION 'brief owner required'; END IF;
  IF brief.status NOT IN ('open','review') THEN RAISE EXCEPTION 'brief is not awardable'; END IF;
  IF sub.status NOT IN ('submitted','shortlisted') THEN RAISE EXCEPTION 'submission is not awardable'; END IF;
  SELECT * INTO latest FROM rights_manifests WHERE rights_manifests.asset_snapshot_id=sub.asset_snapshot_id ORDER BY version DESC LIMIT 1;
  IF latest.id IS NULL OR latest.id<>sub.rights_manifest_id THEN RAISE EXCEPTION 'submission rights manifest is stale'; END IF;
  IF COALESCE((latest.manifest->>'legal_hold')::boolean,false)
     OR COALESCE(latest.manifest->'capabilities'->'commercial_use'->>'status','unknown')<>'allowed'
     OR COALESCE(latest.manifest->'capabilities'->'license'->>'status','unknown')<>'allowed' THEN
    RAISE EXCEPTION 'submission rights are not licensable';
  END IF;
  IF EXISTS(SELECT 1 FROM moderation_cases c WHERE c.workspace_id=sub.submitting_workspace_id AND c.subject_type='asset' AND c.subject_id=sub.asset_snapshot_id::text AND (c.legal_hold OR c.status IN ('open','triage','restricted','appealed'))) THEN
    RAISE EXCEPTION 'submission asset is restricted';
  END IF;
  RETURN QUERY SELECT sub.id,brief.id,brief.workspace_id,sub.submitting_workspace_id,
    sub.asset_snapshot_id,sub.rights_manifest_id,brief.budget_amount,brief.currency,brief.title,brief.requirements;
END $$;
