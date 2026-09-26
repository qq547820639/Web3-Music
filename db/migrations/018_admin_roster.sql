-- 018_admin_roster.sql
-- 平台管理员的跨租户名册读通道（管理端「工作区」视图的数据源）。
--
-- 先说清楚库层现在到底管了什么：实测 `pg_class` 对 users / workspaces / workspace_members 三张表给出
-- relrowsecurity=f、relforcerowsecurity=f —— 001:471-479 的 ENABLE+FORCE ROW LEVEL SECURITY 循环只覆盖
-- 16 张业务表，身份三表不在其中，也没有任何一条策略写在它们身上（全库 grep `POLICY ... ON users|
-- workspaces|workspace_members` 零命中）。而 001:531 把这三张表的 SELECT 授予了 music_app，
-- 实测 `information_schema.role_table_grants` 对 music_app 也只有 SELECT（写入被"从未发出的 GRANT"
-- 挡住，不是被 RLS 挡住，这是 017 那四个 SECURITY DEFINER 函数存在的原因）。
--
-- 所以：跨租户读这份名册在库层从来不是障碍，"要不要给"这件事如果只写在 Python 的依赖里，就只剩
-- Python 一处负责。这条读出去的是别的租户里真人的姓名与邮箱，因此判定放在数据旁边：函数第一句就查
-- 调用者的 is_platform_admin，不是平台管理员直接 RAISE，FastAPI 的 require_platform_admin 是第二道。
-- 与 011/012/017 的写载体同族；读侧的先例是本仓已有的 platform_payout_queue()（同样是平台管理员
-- 的跨租户读）。迁移角色 music_admin 实测 rolsuper=t、rolbypassrls=t，所以 definer 侧读得到全部租户。
--
-- 刻意不给这两张表加 RLS：那要改动登录路径（music_app 必须按邮箱读 users），风险面比这条新读大得多，
-- 而且把"平台管理员可读"写成策略又要引入 actor 身份的 GUC——本仓 API 侧只有 app.workspace_id 一条
-- set_config（services/api/app/db.py:106），没有 actor 用户 id 的通路。

CREATE OR REPLACE FUNCTION platform_workspaces(actor_user uuid)
RETURNS TABLE (workspace_id uuid, workspace_name text, plan text, workspace_status text,
               workspace_created_at timestamptz, member_count bigint,
               owner_user_id uuid, owner_display_name text, owner_email text)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=public AS $$
DECLARE admin boolean;
BEGIN
  -- qualified on purpose: `is_platform_admin` is also an OUT column of this very function, and
  -- plpgsql would otherwise refuse the statement with "column reference ... is ambiguous".
  SELECT users.is_platform_admin INTO admin FROM users WHERE id = actor_user;
  IF NOT coalesce(admin, false) THEN
    RAISE EXCEPTION 'platform administrator required';
  END IF;
  RETURN QUERY
  SELECT w.id, w.name, w.plan, w.status, w.created_at,
         (SELECT count(*) FROM workspace_members m WHERE m.workspace_id = w.id),
         o.user_id, u.display_name, u.email
  FROM workspaces w
  LEFT JOIN workspace_members o ON o.workspace_id = w.id AND o.role = 'owner'
  LEFT JOIN users u ON u.id = o.user_id
  ORDER BY w.created_at, w.name;
END $$;

-- target_workspace 不传就等于"看全部"，所以调用方拿到的是逐工作区的名册而不是一个大列表；
-- 管理端一次只展开一个，避免把整站的成员表一次性搬到浏览器里。
CREATE OR REPLACE FUNCTION platform_workspace_members(actor_user uuid, target_workspace uuid)
RETURNS TABLE (user_id uuid, display_name text, email text, account_status text,
               is_platform_admin boolean, member_role text, joined_at timestamptz)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=public AS $$
DECLARE admin boolean; known uuid;
BEGIN
  -- qualified on purpose: `is_platform_admin` is also an OUT column of this very function, and
  -- plpgsql would otherwise refuse the statement with "column reference ... is ambiguous".
  SELECT users.is_platform_admin INTO admin FROM users WHERE id = actor_user;
  IF NOT coalesce(admin, false) THEN
    RAISE EXCEPTION 'platform administrator required';
  END IF;
  SELECT id INTO known FROM workspaces WHERE id = target_workspace;
  IF known IS NULL THEN
    RAISE EXCEPTION 'workspace not found';
  END IF;
  RETURN QUERY
  SELECT u.id, u.display_name, u.email, u.status, u.is_platform_admin, m.role, m.created_at
  FROM workspace_members m
  JOIN users u ON u.id = m.user_id
  WHERE m.workspace_id = target_workspace
  ORDER BY (m.role = 'owner') DESC, u.display_name, u.email;
END $$;

-- 主体自助删除会连带删掉成员关系（013/014/016 的 erase_user_identity 报 memberships_removed），
-- 所以这份名册里不会出现已删除主体的行；不为此加过滤条件，但演练每次核一遍两侧行数相等。
REVOKE ALL ON FUNCTION platform_workspaces(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION platform_workspace_members(uuid, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION platform_workspaces(uuid) TO music_app;
GRANT EXECUTE ON FUNCTION platform_workspace_members(uuid, uuid) TO music_app;

-- 实测教训：第一版没写限定，`platform_workspace_members` 一调用就 ERROR: column reference
-- "is_platform_admin" is ambiguous —— 它既是本函数的一个 OUT 列名，又是 users 的列名。函数体里
-- 的判定与返回列同名时，plpgsql 会拒绝而不是替你选。017 的写函数没有这个形状（OUT 是 jsonb）。
