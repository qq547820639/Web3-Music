-- 019_workspace_invitations.sql
-- 加入工作区改为"先邀请、当事人自己接受"。017 的按邮箱直接加人同时泄露了两件事，两条都在这里关掉。
--
-- 实测过的缺陷形状（读的是自己仓库的源码，不是推测）：add_workspace_member 在
-- db/migrations/017_workspace_membership.sql:58-61 用
-- `SELECT id, status FROM users WHERE lower(email)=lower(coalesce(member_email,''))` 解析收件人，
-- NOT FOUND 就 RAISE 'no account with that email on this platform'（同文件 60 行）。于是任何一个
-- 工作区的 owner/admin 都能把这个措辞当成员资格探针：给一个地址，看它是被拒还是成功，就知道这个
-- 邮箱在平台上有没有账号。更重的一半是：命中之后成员行当场写进 workspace_members（同文件 71 行），
-- 被挂靠的人没有任何一步同意过——本仓没有邮件/邀请/确认通道（grep smtp、sendgrid、mailgun、ses、
-- nodemailer 零消费者，2026-09-26 复核），也没有公开注册入口（services/api/app/main.py 只有
-- /api/auth/login 与 /api/auth/refresh，没有 register）。
--
-- 这一条迁移把"谁有权成为成员"从"归属者声明"换成"当事人接受"：
--   * 建邀请只写 workspace_invitations，一个字都不碰 workspace_members；
--   * 地址有没有账号在这里根本不被解析（不是解析了再统一措辞，是没有那次查询），
--     所以未知地址与已知未加入地址走出的是同一个对象、同一组键；
--   * 成员资格只在 accept_workspace_invitation 里产生，而它的授权条件是"登录账号自己的邮箱等于
--     邀请邮箱"，写在 SQL 里；token 只证明链接送到了，它不能替人同意。
--
-- 借鉴的三家先例（本轮亲手读过源码/文档，逐条出处记在 docs/FINAL_RELEASE_STATUS.md 与本轮调研稿）：
-- 邀请记录绝不是成员行（Zulip 把 PreregistrationUser/MultiuseInvite 与 UserProfile 分表，
-- filter_to_valid_prereg_users 还要 confirmation__expiry_date >= now()）、固定有效期（GitHub 组织
-- 邀请七天自动过期）、接受方必须持有那个已验证地址（GitHub 的 verified email 匹配）。刻意不学
-- Gitea 的 TeamInvite：它接受时只验 token 命中，routers/web/org/teams.go 的 TeamInvitePost 直接
-- AddTeamMember(ctx, team, ctx.Doer)，从不比较 ctx.Doer 的邮箱与 invite.Email——纯凭"链接在谁手上"
-- 决定成员资格，正是这里要关掉的口子。
--
-- 载体沿用 017/018 的理由：身份三表没有 RLS（001:471-479 的 ENABLE+FORCE 循环只覆盖业务表数组），
-- music_app 对它们只有 SELECT（001:531），所以写入只能走 SECURITY DEFINER 函数。
-- workspace_invitations 由迁移角色 music_admin 建立，写权限一条都不发（文件末尾只有 SELECT 与
-- EXECUTE），因此每一次写都经过下面六条函数；读侧沿用三表的形状，见文件末尾那条 GRANT 的注释。
--
-- 有效期为什么放在 SQL 而不是端点：expires_at 在 INSERT 之前由同一条函数算出（now() + interval
-- '7 days'），接受时又把 expires_at > now() 作为"仍然有效"谓词的一部分——判定与写在同一条语句可达的
-- 范围内，服务端时钟是唯一时区，Python 不参与过期判定，多一个调用者也不多一条可绕的路。

CREATE TABLE workspace_invitations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  email text NOT NULL,
  role text NOT NULL,
  token_hash char(64) NOT NULL UNIQUE,
  created_by uuid NOT NULL REFERENCES users(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL,
  used_at timestamptz,
  used_by uuid REFERENCES users(id),
  revoked_at timestamptz,
  revoked_by uuid REFERENCES users(id),
  CONSTRAINT invitation_expiry_after_creation CHECK (expires_at > created_at),
  CONSTRAINT invitation_single_settlement CHECK (NOT (used_at IS NOT NULL AND revoked_at IS NOT NULL)),
  CONSTRAINT invitation_use_paired CHECK ((used_at IS NULL) = (used_by IS NULL)),
  CONSTRAINT invitation_revoke_paired CHECK ((revoked_at IS NULL) = (revoked_by IS NULL))
);

-- 一个地址在一个工作区里同时只能有一份未落定的邀请；重复邀请走"先作废旧的、再插新的"。
CREATE UNIQUE INDEX workspace_invitations_live_pair
  ON workspace_invitations (workspace_id, lower(email))
  WHERE used_at IS NULL AND revoked_at IS NULL;
-- 名册与收件箱两条读路径都按"未落定"过滤，故各留一个同谓词的部分索引。
CREATE INDEX workspace_invitations_workspace_live
  ON workspace_invitations (workspace_id) WHERE used_at IS NULL AND revoked_at IS NULL;
CREATE INDEX workspace_invitations_email_live
  ON workspace_invitations (lower(email)) WHERE used_at IS NULL AND revoked_at IS NULL;

-- 邀请一旦写下，内容不可再改；落定只能单向发生。与 014 把 auth_sessions 的不可变列钉住同源：
-- "再邀请一次"是开新行（新 token、新有效期），不是在旧行上续期——否则一份已被接受的邀请会变成
-- 可反复改写的对象，审计里那条 expires_at 也就成了装饰。
CREATE OR REPLACE FUNCTION guard_workspace_invitation_update() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.id IS DISTINCT FROM OLD.id OR NEW.workspace_id IS DISTINCT FROM OLD.workspace_id
     OR NEW.email IS DISTINCT FROM OLD.email OR NEW.role IS DISTINCT FROM OLD.role
     OR NEW.token_hash IS DISTINCT FROM OLD.token_hash OR NEW.created_by IS DISTINCT FROM OLD.created_by
     OR NEW.created_at IS DISTINCT FROM OLD.created_at OR NEW.expires_at IS DISTINCT FROM OLD.expires_at THEN
    RAISE EXCEPTION 'an invitation''s address, role, token and lifetime are immutable';
  END IF;
  IF (OLD.used_at IS NOT NULL AND (NEW.used_at IS DISTINCT FROM OLD.used_at OR NEW.used_by IS DISTINCT FROM OLD.used_by))
     OR (OLD.revoked_at IS NOT NULL AND (NEW.revoked_at IS DISTINCT FROM OLD.revoked_at OR NEW.revoked_by IS DISTINCT FROM OLD.revoked_by)) THEN
    RAISE EXCEPTION 'a settled invitation cannot be re-opened';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER workspace_invitations_update_guard BEFORE UPDATE ON workspace_invitations
  FOR EACH ROW EXECUTE FUNCTION guard_workspace_invitation_update();

-- 主体行使被遗忘权时把指向旧地址的未落定邀请一并作废。013/016 的 erase_user_identity 已经把
-- users.email 改写成 erased-<uuid>@invalid.invalid（013:94）并删掉成员行（013:90），但邀请行是按
-- 邮箱而不是按 user_id 存的——没有这道触发器，一个已 erased 的人的真实邮箱会继续留在库里直到过期，
-- 而"账号已不存在"与"这条邀请还活着"两件事互相矛盾。
-- 同一道触发器顺带钉住一个更一般的事实：改邮箱等于放弃旧地址上的待接受邀请，要保留就重新邀请。
-- 为什么用触发器而不是改 erase_user_identity：013/016 的内容已被 services/migrate/migrate.py 的
-- SHA-256 校验和冻结（改一个字节就拒绝启动），能新增的只有这一条。
CREATE OR REPLACE FUNCTION users_email_change_revokes_invitations() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=public AS $$
DECLARE settled integer;
BEGIN
  UPDATE workspace_invitations SET revoked_at=now(), revoked_by=NEW.id
   WHERE lower(email)=lower(OLD.email) AND used_at IS NULL AND revoked_at IS NULL;
  GET DIAGNOSTICS settled = ROW_COUNT;
  RAISE NOTICE 'invitations revoked for the previous address: %', settled;
  RETURN NEW;
END $$;

CREATE TRIGGER users_email_change_revokes_invitations BEFORE UPDATE OF email ON users
  FOR EACH ROW WHEN (OLD.email IS DISTINCT FROM NEW.email)
  EXECUTE FUNCTION users_email_change_revokes_invitations();

-- ---------------------------------------------------------------- 建邀请 ----
-- 措辞纪律：这里没有对 users 按邮箱的那次查询，所以"存在与否"无从影响响应。唯一一处看起来在回答
-- 存在性的分支是 already_member，而调用方本来就能从 GET /api/workspace/members 的名册里看到同一件事
-- （018 之后平台管理员还能逐工作区看全租户），它不新增可探测的事实。
CREATE OR REPLACE FUNCTION create_workspace_invitation(
  target_workspace uuid, actor_user uuid, member_email text, member_role text, invitation_token_hash text)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=public AS $$
DECLARE workspace_state text; actor_role text; admin boolean; normalized text;
        already record; settled integer; created uuid; lifetime timestamptz;
BEGIN
  SELECT status INTO workspace_state FROM workspaces WHERE id=target_workspace;
  IF workspace_state IS NULL THEN
    RAISE EXCEPTION 'workspace not found';
  END IF;
  IF workspace_state <> 'active' THEN
    RAISE EXCEPTION 'only an active workspace can take members';
  END IF;
  -- qualified on purpose: 与 018:28-30 同一个坑，本函数体外层调用者可能带同名列。
  SELECT users.is_platform_admin INTO admin FROM users WHERE id=actor_user;
  SELECT role INTO actor_role FROM workspace_members WHERE workspace_id=target_workspace AND user_id=actor_user;
  IF NOT coalesce(admin, false) AND (actor_role IS NULL OR actor_role NOT IN ('owner','admin')) THEN
    RAISE EXCEPTION 'workspace owner or admin required';
  END IF;
  IF workspace_role_error(member_role) IS NOT NULL THEN
    RAISE EXCEPTION '%', workspace_role_error(member_role);
  END IF;
  -- 不复述合法角色清单（017:23-25 的理由照抄：抄一份就是第二个真值源，而说谎的是副本）。
  -- 只单独钉住"邀请不能给所有权"：归属转移要求现任 owner 亲自行动（017:183-186），
  -- 一条能写出 owner 的邀请会绕过那条判定。
  IF member_role = 'owner' THEN
    RAISE EXCEPTION 'an invitation cannot offer workspace ownership; transfer it instead';
  END IF;

  normalized := lower(btrim(coalesce(member_email, '')));
  IF normalized = '' OR position('@' in normalized) < 2 OR normalized ~ '[[:space:]]' OR length(normalized) > 320 THEN
    RAISE EXCEPTION 'an email address is required';
  END IF;
  IF invitation_token_hash IS NULL OR invitation_token_hash !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'a hashed invitation token is required';
  END IF;

  -- 成员判定 join 到 users，而不是解析邮箱：命中即"这个地址已经是本工作区成员"。
  SELECT m.user_id, m.role INTO already
    FROM workspace_members m JOIN users u ON u.id=m.user_id
   WHERE m.workspace_id=target_workspace AND lower(u.email)=normalized
   LIMIT 1;
  IF already.user_id IS NOT NULL THEN
    RETURN jsonb_build_object('invited', false, 'already_member', true, 'user_id', already.user_id::text,
                              'email', normalized, 'role', already.role);
  END IF;

  lifetime := now() + interval '7 days';
  -- 同一地址重复邀请：先作废旧的未落定行，再插新行。两步之间被并发插队会撞
  -- workspace_invitations_live_pair，那正是这个部分唯一索引存在的目的。
  UPDATE workspace_invitations SET revoked_at=now(), revoked_by=actor_user
   WHERE workspace_id=target_workspace AND lower(email)=normalized AND used_at IS NULL AND revoked_at IS NULL;
  GET DIAGNOSTICS settled = ROW_COUNT;

  INSERT INTO workspace_invitations(workspace_id, email, role, token_hash, created_by, expires_at)
    VALUES (target_workspace, normalized, member_role, invitation_token_hash, actor_user, lifetime)
    RETURNING id INTO created;

  -- 响应里没有 token 明文（库里从来只有摘要），也没有"这个邮箱有没有账号"的任何痕迹：
  -- 未知地址与已知未加入地址到这里走出的是同一个对象、同一组键。
  RETURN jsonb_build_object('invited', true, 'already_member', false, 'invitation_id', created::text,
                            'email', normalized, 'role', member_role, 'expires_at', lifetime,
                            'superseded', settled);
END $$;

-- ---------------------------------------------------------------- 两份读 ----
-- OUT 列一律不与被 join 的列同名：018 的实测教训是 plpgsql 直接拒绝函数体
-- （column reference ... is ambiguous），限定前缀只解决了一半，同名本身才是根因。
CREATE OR REPLACE FUNCTION workspace_invitation_list(target_workspace uuid, actor_user uuid)
RETURNS TABLE (invitation_id uuid, invited_email text, invitation_role text, invitation_status text,
               invited_by_user uuid, invited_by_name text, invited_by_email text,
               invited_at timestamptz, invitation_expires_at timestamptz,
               settled_at timestamptz, settled_by_name text)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=public AS $$
DECLARE workspace_state text; actor_role text; admin boolean;
BEGIN
  SELECT status INTO workspace_state FROM workspaces WHERE id=target_workspace;
  IF workspace_state IS NULL THEN
    RAISE EXCEPTION 'workspace not found';
  END IF;
  SELECT users.is_platform_admin INTO admin FROM users WHERE id=actor_user;
  SELECT role INTO actor_role FROM workspace_members WHERE workspace_id=target_workspace AND user_id=actor_user;
  IF NOT coalesce(admin, false) AND (actor_role IS NULL OR actor_role NOT IN ('owner','admin')) THEN
    RAISE EXCEPTION 'workspace owner or admin required';
  END IF;
  RETURN QUERY
  SELECT i.id, i.email, i.role,
         CASE WHEN i.used_at IS NOT NULL THEN 'used'
              WHEN i.revoked_at IS NOT NULL THEN 'revoked'
              WHEN i.expires_at <= now() THEN 'expired'
              ELSE 'pending' END,
         i.created_by, u.display_name, u.email, i.created_at, i.expires_at,
         coalesce(i.used_at, i.revoked_at), s.display_name
  FROM workspace_invitations i
  JOIN users u ON u.id = i.created_by
  LEFT JOIN users s ON s.id = coalesce(i.used_by, i.revoked_by)
  WHERE i.workspace_id = target_workspace
  ORDER BY (i.used_at IS NULL AND i.revoked_at IS NULL AND i.expires_at > now()) DESC, i.created_at DESC;
END $$;

-- 受邀者自己的收件箱：筛选条件是"登录账号的邮箱"，不是"谁手上有链接"。它跨租户，
-- 但只横向看到"有人邀请了我"这一件事——那封邀请本来就是发给这个地址的。
CREATE OR REPLACE FUNCTION my_workspace_invitations(actor_user uuid)
RETURNS TABLE (invitation_id uuid, invitation_workspace uuid, workspace_name text, invitation_role text,
               invited_by_name text, invitation_created_at timestamptz, invitation_expires_at timestamptz)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=public AS $$
DECLARE own text;
BEGIN
  SELECT lower(users.email) INTO own FROM users WHERE id=actor_user;
  IF own IS NULL THEN
    RAISE EXCEPTION 'account unavailable';
  END IF;
  RETURN QUERY
  SELECT i.id, i.workspace_id, w.name, i.role, u.display_name, i.created_at, i.expires_at
  FROM workspace_invitations i
  JOIN workspaces w ON w.id = i.workspace_id
  JOIN users u ON u.id = i.created_by
  WHERE lower(i.email) = own AND i.used_at IS NULL AND i.revoked_at IS NULL AND i.expires_at > now()
    AND w.status = 'active'
  ORDER BY i.created_at DESC;
END $$;

-- ---------------------------------------------------------------- 撤销 ----
CREATE OR REPLACE FUNCTION revoke_workspace_invitation(target_workspace uuid, actor_user uuid, invitation_id uuid)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=public AS $$
DECLARE workspace_state text; actor_role text; admin boolean; row record;
BEGIN
  SELECT status INTO workspace_state FROM workspaces WHERE id=target_workspace;
  IF workspace_state IS NULL THEN
    RAISE EXCEPTION 'workspace not found';
  END IF;
  SELECT users.is_platform_admin INTO admin FROM users WHERE id=actor_user;
  SELECT role INTO actor_role FROM workspace_members WHERE workspace_id=target_workspace AND user_id=actor_user;
  IF NOT coalesce(admin, false) AND (actor_role IS NULL OR actor_role NOT IN ('owner','admin')) THEN
    RAISE EXCEPTION 'workspace owner or admin required';
  END IF;
  SELECT * INTO row FROM workspace_invitations WHERE id=invitation_id AND workspace_id=target_workspace FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'invitation not found';
  END IF;
  IF row.used_at IS NOT NULL OR row.revoked_at IS NOT NULL THEN
    RETURN jsonb_build_object('revoked', false, 'already_settled', true, 'invitation_id', invitation_id::text);
  END IF;
  UPDATE workspace_invitations SET revoked_at=now(), revoked_by=actor_user WHERE id=invitation_id;
  RETURN jsonb_build_object('revoked', true, 'already_settled', false, 'invitation_id', invitation_id::text,
                            'email', row.email, 'role', row.role);
END $$;

-- 受邀人自己说"不去"。授权条件与接受同形：登录账号的邮箱必须就是邀请邮箱，否则你既不能替别人
-- 接受，也不能替别人回绝。它写的是 revoked_at/revoked_by=本人，所以一份被回绝的邀请与一份被撤销的
-- 邀请落在同一列上——区别在是谁落定的，那由 settled_by_name 与审计行给出，不必再加一个枚举列。
CREATE OR REPLACE FUNCTION decline_workspace_invitation(actor_user uuid, invitation_id uuid)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=public AS $$
DECLARE row record; own text;
BEGIN
  SELECT * INTO row FROM workspace_invitations WHERE id=invitation_id FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'invitation not found';
  END IF;
  SELECT lower(users.email) INTO own FROM users WHERE id=actor_user;
  IF own IS DISTINCT FROM lower(row.email) THEN
    RAISE EXCEPTION 'this invitation was addressed to a different account';
  END IF;
  IF row.used_at IS NOT NULL THEN
    RAISE EXCEPTION 'that invitation has already been used';
  END IF;
  IF row.revoked_at IS NOT NULL THEN
    RETURN jsonb_build_object('declined', false, 'already_settled', true, 'invitation_id', invitation_id::text);
  END IF;
  UPDATE workspace_invitations SET revoked_at=now(), revoked_by=actor_user WHERE id=invitation_id;
  RETURN jsonb_build_object('declined', true, 'already_settled', false, 'invitation_id', invitation_id::text,
                            'workspace_id', row.workspace_id::text, 'role', row.role);
END $$;

-- ---------------------------------------------------------------- 接受 ----
-- 唯一把邀请变成成员资格的地方，所以"仍然有效"的三条谓词（未使用、未撤销、未过期）都写在这条
-- 语句可达的范围里，而不是先读一遍再由调用方决定要不要写。邮箱匹配是这里的授权条件。
CREATE OR REPLACE FUNCTION accept_workspace_invitation(actor_user uuid, invitation_id uuid, invitation_token_hash text)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=public AS $$
DECLARE row record; account record; ws_state text; member_role text; added uuid;
BEGIN
  IF (invitation_id IS NULL) = (invitation_token_hash IS NULL) THEN
    RAISE EXCEPTION 'claim an invitation by either its id or its token, not both and not neither';
  END IF;
  IF invitation_token_hash IS NOT NULL AND invitation_token_hash !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'a hashed invitation token is required';
  END IF;

  -- FOR UPDATE：同一份邀请被点两次时，第二个事务在这里排队，醒来后 live 谓词已经不成立。
  SELECT * INTO row FROM workspace_invitations
   WHERE (invitation_id IS NOT NULL AND id = invitation_id)
      OR (invitation_token_hash IS NOT NULL AND token_hash = invitation_token_hash)
   FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'invitation not found';
  END IF;
  IF row.used_at IS NOT NULL THEN
    RAISE EXCEPTION 'that invitation has already been used';
  END IF;
  IF row.revoked_at IS NOT NULL THEN
    RAISE EXCEPTION 'that invitation has been withdrawn';
  END IF;
  IF row.expires_at <= now() THEN
    RAISE EXCEPTION 'that invitation has expired';
  END IF;

  SELECT users.id, users.status, lower(users.email) AS email INTO account FROM users WHERE id=actor_user;
  IF account.id IS NULL THEN
    RAISE EXCEPTION 'account unavailable';
  END IF;
  IF account.status <> 'active' THEN
    RAISE EXCEPTION 'that account cannot be added because it is not active';
  END IF;
  IF account.email <> lower(row.email) THEN
    RAISE EXCEPTION 'this invitation was addressed to a different account';
  END IF;

  SELECT w.status INTO ws_state FROM workspaces w WHERE w.id=row.workspace_id;
  IF ws_state IS DISTINCT FROM 'active' THEN
    RAISE EXCEPTION 'only an active workspace can take members';
  END IF;

  UPDATE workspace_invitations SET used_at=now(), used_by=actor_user WHERE id=row.id;

  SELECT m.role INTO member_role FROM workspace_members m
   WHERE m.workspace_id=row.workspace_id AND m.user_id=actor_user;
  IF member_role IS NOT NULL THEN
    RETURN jsonb_build_object('accepted', true, 'added', false, 'already_member', true,
                              'invitation_id', row.id::text, 'workspace_id', row.workspace_id::text,
                              'role', member_role);
  END IF;

  -- 角色合法性不在这里复述：workspace_members 的 CHECK（001:24）才是真值源，让它开火。
  -- ON CONFLICT 只兜并发下的重复主键（上面那次 SELECT 与 INSERT 之间有窗口），
  -- DO NOTHING 之后重读一次真实角色，绝不自报一个没写进去的值。
  INSERT INTO workspace_members(workspace_id, user_id, role) VALUES (row.workspace_id, actor_user, row.role)
    ON CONFLICT (workspace_id, user_id) DO NOTHING
    RETURNING workspace_members.user_id INTO added;
  IF added IS NULL THEN
    SELECT m.role INTO member_role FROM workspace_members m
     WHERE m.workspace_id=row.workspace_id AND m.user_id=actor_user;
    RETURN jsonb_build_object('accepted', true, 'added', false, 'already_member', true,
                              'invitation_id', row.id::text, 'workspace_id', row.workspace_id::text,
                              'role', member_role);
  END IF;
  RETURN jsonb_build_object('accepted', true, 'added', true, 'already_member', false,
                            'invitation_id', row.id::text, 'workspace_id', row.workspace_id::text,
                            'role', row.role, 'email', row.email);
END $$;

-- 017 的按邮箱直接加人到此不再有合法调用者：探针与未经同意的挂靠都从那一条函数出发。
-- 不复用它、不包一层"看起来安全"的壳，而是把 music_app 对它的 EXECUTE 收回来——
-- 留着就等于邀请旁边始终开着一扇不需要同意的门。函数体本身留在 017 里（校验和冻结，改不动），
-- 但应用角色再也调不到它；scripts/member_drill.py 与 tests/unit 各自钉住这一条。
REVOKE EXECUTE ON FUNCTION add_workspace_member(uuid, uuid, text, text) FROM music_app;

REVOKE ALL ON FUNCTION guard_workspace_invitation_update() FROM PUBLIC;
REVOKE ALL ON FUNCTION users_email_change_revokes_invitations() FROM PUBLIC;
REVOKE ALL ON FUNCTION create_workspace_invitation(uuid, uuid, text, text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION workspace_invitation_list(uuid, uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION my_workspace_invitations(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION revoke_workspace_invitation(uuid, uuid, uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION decline_workspace_invitation(uuid, uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION accept_workspace_invitation(uuid, uuid, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION create_workspace_invitation(uuid, uuid, text, text, text) TO music_app;
GRANT EXECUTE ON FUNCTION workspace_invitation_list(uuid, uuid) TO music_app;
GRANT EXECUTE ON FUNCTION my_workspace_invitations(uuid) TO music_app;
GRANT EXECUTE ON FUNCTION revoke_workspace_invitation(uuid, uuid, uuid) TO music_app;
GRANT EXECUTE ON FUNCTION decline_workspace_invitation(uuid, uuid) TO music_app;
GRANT EXECUTE ON FUNCTION accept_workspace_invitation(uuid, uuid, text) TO music_app;
-- 读侧沿用身份三表的形状（001:531 给 music_app SELECT）：名册与收件箱仍然走上面那两条 STABLE 函数，
-- 因为"谁能看"的判定不在表上；这条 GRANT 只为 /api/account/export 的逐表 SELECT 存在。
GRANT SELECT ON workspace_invitations TO music_app;
