-- Promote a paid licence's reservation under the offer row lock (release gate G12-a).
--
-- market.py used to write offer_reservations and asset_offers directly from the buyer's
-- transaction. Both tables are RLS-protected for the tenant, and the buyer is not the
-- seller's tenant, so those UPDATEs matched zero rows and reported no error: an exclusive
-- offer was never flipped to 'sold' by the buyer who paid for it, and scripts/reservation_race.py
-- showed a stale, already-expired reservation being paid into a second active licence.
--
-- Doing it here keeps the authority check in one place, next to reserve_marketplace_offer,
-- and adds the source-state predicates the old statements lacked.
CREATE OR REPLACE FUNCTION confirm_marketplace_offer_reservation(target_offer uuid,target_order uuid,target_buyer uuid)
RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=public AS $$
DECLARE offer_row asset_offers;
BEGIN
  SELECT * INTO offer_row FROM asset_offers WHERE id=target_offer FOR UPDATE;
  IF offer_row.id IS NULL THEN RAISE EXCEPTION 'offer not found'; END IF;
  UPDATE offer_reservations SET status='confirmed',updated_at=now()
    WHERE order_id=target_order AND buyer_workspace_id=target_buyer AND status='pending';
  IF NOT FOUND THEN RAISE EXCEPTION 'reservation is no longer pending for this order'; END IF;
  IF offer_row.exclusive THEN
    UPDATE asset_offers SET status='sold',updated_at=now() WHERE id=target_offer AND status='active';
    IF NOT FOUND THEN RAISE EXCEPTION 'exclusive offer is no longer active'; END IF;
  END IF;
END $$;

REVOKE ALL ON FUNCTION confirm_marketplace_offer_reservation(uuid,uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION confirm_marketplace_offer_reservation(uuid,uuid,uuid) TO music_app;
