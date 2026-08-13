-- 008_revenue_split_trigger_security_definer.sql
-- check_revenue_split_total() fires after revenue_splits writes but runs as the
-- invoking role (music_app) with that request's tenant context, so RLS hides the
-- split rows written by record_license_revenue() for a DIFFERENT (seller)
-- workspace. The sum reads as 0 and the deferred trigger raises
-- "revenue split total must equal 10000 basis points, got 0", which aborts the
-- license fulfillment (payment webhook) at commit. Recreate the function as
-- SECURITY DEFINER so the total check sees all rows regardless of tenant.

CREATE OR REPLACE FUNCTION check_revenue_split_total() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=public AS $$
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
