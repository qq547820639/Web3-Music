-- Workspace membership and role management (G11): the write path the API never had.
--
-- Why these have to be SECURITY DEFINER functions rather than application UPDATEs, measured
-- against the live stack rather than assumed: music_app holds SELECT only on both `workspaces` and
-- `workspace_members` (db/migrations/001_production_candidate.sql:531), so every statement an
-- endpoint would need matches zero rows and raises nothing -- the same silent-success shape that
-- made 011 and 012 necessary on the reservation and refund paths. Widening the grant is the wrong
-- fix: it would let any code path in the API rewrite who owns a workspace.
--
-- Why this closes a hole that already shipped: erase_user_identity refuses to erase an account that
-- is the only owner of a workspace and tells the caller to "transfer ownership first"
-- (db/migrations/014_erase_keeps_session_expiry_immutable.sql, and 016 kept that text). Until this
-- migration there was no API, endpoint or function anywhere in the repository that could do the
-- transfer, so the right-to-erasure path dead-ended with instructions to perform an operation only a
-- database superuser could do. scripts/member_drill.py now walks that loop end to end.
--
-- The invariants, and why each is where it is:
--   * "at least one owner" is enforced here rather than in the endpoint, because it is a property of
--     the table, not of one call path: demotion, removal and erasure (014/016) all threaten it, and
--     the last two already guard it with the same predicate. Ownership transfer is one function
--     because promoting and demoting in two statements would leave a window with zero owners if the
--     second write failed -- and it promotes first for the same reason.
--   * the legal role names are NOT restated here. `role text NOT NULL CHECK(role IN (...))` at
--     001:24 is the authority; the functions let the constraint fire and translate it. A copied list
--     would be a second place to keep in sync, and it would be the copy that lies.
--   * the actor's authority is re-derived from workspace_members inside the function from the actor's
--     own id, which the endpoint took from the verified token. require_roles() in the API is a
--     convenience; it is not the guarantee.

CREATE OR REPLACE FUNCTION workspace_role_error(role text) RETURNS text
LANGUAGE sql STABLE AS $$
  SELECT CASE WHEN role IS NULL THEN 'a workspace role is required'
              WHEN role IN ('owner','admin','creator','reviewer','viewer','billing','legal','support') THEN NULL
              ELSE 'unknown workspace role' END
$$;

CREATE OR REPLACE FUNCTION add_workspace_member(target_workspace uuid, actor_user uuid, member_email text, member_role text)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=public AS $$
DECLARE workspace_state text; actor_role text; admin boolean; target record; existing record; added uuid;
BEGIN
  SELECT status INTO workspace_state FROM workspaces WHERE id=target_workspace;
  IF workspace_state IS NULL THEN
    RAISE EXCEPTION 'workspace not found';
  END IF;
  IF workspace_state <> 'active' THEN
    RAISE EXCEPTION 'only an active workspace can take members';
  END IF;
  SELECT is_platform_admin INTO admin FROM users WHERE id=actor_user;
  SELECT role INTO actor_role FROM workspace_members WHERE workspace_id=target_workspace AND user_id=actor_user;
  IF NOT coalesce(admin, false) AND (actor_role IS NULL OR actor_role NOT IN ('owner','admin')) THEN
    RAISE EXCEPTION 'workspace owner or admin required';
  END IF;
  IF workspace_role_error(member_role) IS NOT NULL THEN
    RAISE EXCEPTION '%', workspace_role_error(member_role);
  END IF;

  SELECT id, status INTO target FROM users WHERE lower(email)=lower(coalesce(member_email,''));
  IF NOT FOUND THEN
    RAISE EXCEPTION 'no account with that email on this platform';
  END IF;
  IF target.status <> 'active' THEN
    RAISE EXCEPTION 'that account cannot be added because it is not active';
  END IF;

  SELECT role INTO existing FROM workspace_members WHERE workspace_id=target_workspace AND user_id=target.id;
  IF existing IS NOT NULL THEN
    RETURN jsonb_build_object('added', false, 'already_member', true, 'user_id', target.id::text, 'role', existing);
  END IF;

  INSERT INTO workspace_members(workspace_id,user_id,role) VALUES (target_workspace, target.id, member_role)
    RETURNING user_id INTO added;
  RETURN jsonb_build_object('added', true, 'already_member', false, 'user_id', added::text, 'role', member_role);
END $$;

CREATE OR REPLACE FUNCTION change_workspace_member_role(target_workspace uuid, actor_user uuid, member_user uuid, member_role text)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=public AS $$
DECLARE workspace_state text; actor_role text; admin boolean; previous_role text; target_role text; owners integer;
BEGIN
  SELECT status INTO workspace_state FROM workspaces WHERE id=target_workspace;
  IF workspace_state IS NULL THEN
    RAISE EXCEPTION 'workspace not found';
  END IF;
  IF workspace_state <> 'active' THEN
    RAISE EXCEPTION 'only an active workspace can change roles';
  END IF;
  SELECT is_platform_admin INTO admin FROM users WHERE id=actor_user;
  SELECT role INTO actor_role FROM workspace_members WHERE workspace_id=target_workspace AND user_id=actor_user;
  IF NOT coalesce(admin, false) AND (actor_role IS NULL OR actor_role NOT IN ('owner','admin')) THEN
    RAISE EXCEPTION 'workspace owner or admin required';
  END IF;
  IF workspace_role_error(member_role) IS NOT NULL THEN
    RAISE EXCEPTION '%', workspace_role_error(member_role);
  END IF;

  SELECT role INTO previous_role FROM workspace_members
    WHERE workspace_id=target_workspace AND user_id=member_user FOR UPDATE;
  IF previous_role IS NULL THEN
    RAISE EXCEPTION 'that account is not a member of this workspace';
  END IF;
  IF previous_role = member_role THEN
    RETURN jsonb_build_object('changed', false, 'unchanged', true, 'user_id', member_user::text, 'role', previous_role);
  END IF;

  -- The symmetric rule to the one in remove_workspace_member: with two owners an admin could
  -- otherwise strip the other owner's standing while the count guard stayed quiet.
  IF previous_role = 'owner' AND coalesce(actor_role,'') <> 'owner' THEN
    RAISE EXCEPTION 'only an owner can change another owner''s role';
  END IF;
  -- The last owner cannot be demoted: the same predicate the eraser uses (014/016), so the two paths
  -- cannot disagree about whether a workspace would be left without one.
  IF previous_role = 'owner' THEN
    SELECT count(*) INTO owners FROM workspace_members WHERE workspace_id=target_workspace AND role='owner';
    IF owners <= 1 THEN
      RAISE EXCEPTION 'the only owner of this workspace cannot be demoted; transfer ownership first';
    END IF;
  END IF;

  UPDATE workspace_members SET role=member_role
    WHERE workspace_id=target_workspace AND user_id=member_user
    RETURNING role INTO target_role;
  RETURN jsonb_build_object('changed', true, 'unchanged', false, 'user_id', member_user::text,
                            'role', target_role, 'previous_role', previous_role);
END $$;

CREATE OR REPLACE FUNCTION remove_workspace_member(target_workspace uuid, actor_user uuid, member_user uuid)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=public AS $$
DECLARE workspace_state text; actor_role text; admin boolean; target_role text; owners integer;
BEGIN
  SELECT status INTO workspace_state FROM workspaces WHERE id=target_workspace;
  IF workspace_state IS NULL THEN
    RAISE EXCEPTION 'workspace not found';
  END IF;
  IF workspace_state <> 'active' THEN
    RAISE EXCEPTION 'only an active workspace can lose members';
  END IF;
  SELECT is_platform_admin INTO admin FROM users WHERE id=actor_user;
  SELECT role INTO actor_role FROM workspace_members WHERE workspace_id=target_workspace AND user_id=actor_user;
  IF NOT coalesce(admin, false) AND (actor_role IS NULL OR actor_role NOT IN ('owner','admin')) THEN
    RAISE EXCEPTION 'workspace owner or admin required';
  END IF;

  SELECT role INTO target_role FROM workspace_members
    WHERE workspace_id=target_workspace AND user_id=member_user FOR UPDATE;
  IF target_role IS NULL THEN
    RETURN jsonb_build_object('removed', false, 'was_member', false, 'user_id', member_user::text);
  END IF;
  -- Order matters. A delegated admin asking to evict the owner is refused for the reason that
  -- describes their own standing, not for an accident of how many owners there happen to be; and if
  -- the count check ran first, the same admin would get "transfer ownership first" -- advice they
  -- cannot follow, since they are not the owner.
  IF target_role = 'owner' AND coalesce(actor_role,'') <> 'owner' THEN
    RAISE EXCEPTION 'only an owner can remove another owner';
  END IF;
  IF target_role = 'owner' THEN
    SELECT count(*) INTO owners FROM workspace_members WHERE workspace_id=target_workspace AND role='owner';
    IF owners <= 1 THEN
      RAISE EXCEPTION 'the only owner of this workspace cannot be removed; transfer ownership first';
    END IF;
  END IF;

  DELETE FROM workspace_members WHERE workspace_id=target_workspace AND user_id=member_user;
  RETURN jsonb_build_object('removed', true, 'was_member', true, 'user_id', member_user::text, 'previous_role', target_role);
END $$;

CREATE OR REPLACE FUNCTION transfer_workspace_ownership(target_workspace uuid, actor_user uuid, member_user uuid)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=public AS $$
DECLARE workspace_state text; actor_role text; target_role text; incoming uuid;
BEGIN
  SELECT status INTO workspace_state FROM workspaces WHERE id=target_workspace;
  IF workspace_state IS NULL THEN
    RAISE EXCEPTION 'workspace not found';
  END IF;
  IF workspace_state <> 'active' THEN
    RAISE EXCEPTION 'ownership of an inactive workspace cannot be transferred';
  END IF;
  -- Deliberately stricter than the other three: a platform administrator can add, demote and remove
  -- members, but ownership moves only by the act of an owner. A delegated manager must not be able
  -- to take a workspace, and an operator must not be able to hand one to someone who is not in it.
  SELECT role INTO actor_role FROM workspace_members WHERE workspace_id=target_workspace AND user_id=actor_user;
  IF actor_role IS DISTINCT FROM 'owner' THEN
    RAISE EXCEPTION 'only the current owner can transfer ownership';
  END IF;
  IF actor_user = member_user THEN
    RAISE EXCEPTION 'that account already owns this workspace';
  END IF;
  SELECT role INTO target_role FROM workspace_members WHERE workspace_id=target_workspace AND user_id=member_user;
  IF target_role IS NULL THEN
    RAISE EXCEPTION 'the new owner must already be a member of this workspace';
  END IF;

  UPDATE workspace_members SET role='owner'
    WHERE workspace_id=target_workspace AND user_id=member_user RETURNING user_id INTO incoming;
  UPDATE workspace_members SET role='admin'
    WHERE workspace_id=target_workspace AND user_id=actor_user;
  RETURN jsonb_build_object('transferred', true, 'new_owner', incoming::text, 'previous_owner_role', 'admin',
    'owners', (SELECT count(*)::int FROM workspace_members WHERE workspace_id=target_workspace AND role='owner'));
END $$;

REVOKE ALL ON FUNCTION workspace_role_error(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION add_workspace_member(uuid, uuid, text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION change_workspace_member_role(uuid, uuid, uuid, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION remove_workspace_member(uuid, uuid, uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION transfer_workspace_ownership(uuid, uuid, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION workspace_role_error(text) TO music_app;
GRANT EXECUTE ON FUNCTION add_workspace_member(uuid, uuid, text, text) TO music_app;
GRANT EXECUTE ON FUNCTION change_workspace_member_role(uuid, uuid, uuid, text) TO music_app;
GRANT EXECUTE ON FUNCTION remove_workspace_member(uuid, uuid, uuid) TO music_app;
GRANT EXECUTE ON FUNCTION transfer_workspace_ownership(uuid, uuid, uuid) TO music_app;
