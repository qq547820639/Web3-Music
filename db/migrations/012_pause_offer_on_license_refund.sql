-- Lift a sold exclusive listing back off the "sold" state when its licence is refunded.
--
-- market.py:376 issued this UPDATE from the buyer's transaction, but asset_offers is
-- RLS-protected for the seller's tenant (002 enables FORCE ROW LEVEL SECURITY on it with
-- workspace_id = current_setting('app.workspace_id')), so the buyer cannot see the row: the
-- statement matched zero rows and raised nothing. The refund therefore revoked the delivery
-- and reversed the payout but left the offer 'sold' forever, which keeps it out of
-- marketplace_offers_public and makes the asset unsellable again.
--
-- Same shape as confirm_marketplace_offer_reservation (011): the authority write belongs in
-- a SECURITY DEFINER function next to reserve_marketplace_offer, not in a tenant-scoped
-- application statement.
CREATE OR REPLACE FUNCTION pause_marketplace_offer_on_refund(target_order uuid)
RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=public AS $$
DECLARE offer_id uuid;
BEGIN
  SELECT subject_id::uuid INTO offer_id FROM orders WHERE id=target_order AND subject_type='asset_offer';
  IF offer_id IS NULL THEN RETURN; END IF;
  -- Deliberately preserves the previous intent: only an exclusive listing that is actually
  -- 'sold' is paused, and pausing (rather than re-activating) leaves re-listing to the seller.
  UPDATE asset_offers SET status='paused',updated_at=now()
    WHERE id=offer_id AND exclusive=true AND status='sold';
END $$;

REVOKE ALL ON FUNCTION pause_marketplace_offer_on_refund(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION pause_marketplace_offer_on_refund(uuid) TO music_app;
