-- 021_public_rights_report.sql
-- 权利投诉从「只有员工能开案」改成「任何人都能递，且递进来不会把投诉人交给被投诉人」。
--
-- 本轮先量再写（读数都是亲手取的，不是推测）：
--   * `moderation_cases` 是 RLS + FORCE（pg_class: relrowsecurity=t / relforcerowsecurity=t），
--     策略只有一条 `tenant_policy`：`workspace_id = current_setting('app.workspace_id', true)`（pg_policy 实读）；
--     那个 GUC 由 `services/api/app/db.py:106` 每事务设，**匿名调用者没有它**，
--     所以应用侧直接 INSERT 会撞上 011/012 同一种形状：要么被 WITH CHECK 拒，要么读到零行且不报错。
--   * `music_app` 对该表只有 INSERT/SELECT/UPDATE（information_schema.role_table_grants），没有 DELETE；
--   * 迁移角色 `music_admin` 是 `rolsuper=t / rolbypassrls=t`（pg_roles 实读）——
--     所以 SECURITY DEFINER 函数**不受 RLS 约束**，「谁能做什么」只能写在函数体里，
--     这正是 017/018/019 的每条函数都自带谓词的原因，本文件沿用同一个理由。
--   * 租户侧看得见案件全文：`services/api/app/routers/assets.py:232` 是 `SELECT * FROM moderation_cases`。
--     于是投诉人邮箱一旦进 `evidence`，就等于把举报人交给被举报方——这是本文件把 PII 关进新表的直接原因，
--     也是 GitHub 政策里「redacted filings」那一手在防的东西（本轮亲手打开过该页）。
--
-- 这一条迁移换掉三件事：
--   1. 受理入口不再要求账号：`file_rights_report` 是公开写通道，任何人（含没有本仓账号的权利人）
--      都能递进一份通知，并当场拿到一份**回执口令**；本仓没有邮件通道（smtp/sendgrid/mailgun/ses/
--      nodemailer 零消费者，2026-09-26 复核），所以口令只在响应里出现一次，之后凭 SHA-256 比对查询。
--   2. 通知必须带着两条声明才算通知：`attested_statements @> ARRAY['good_faith','accuracy']` 写在表的
--      CHECK 上（DSA 第 16 条要求的"善意确信"与"陈述真实"，条文引文记在调研稿；GitHub 那页额外要
--      "penalty of perjury"，本表用同一个数组容纳，将来加一项不必改结构）。端点先给 422，表的 CHECK
--      是兜底——它保证没有任何一条调用路径能写进一份没声明的通知。
--   3. 受理不回答「这个 id 在不在」：能解析出归属工作区的，当场开案（`case_id` 落库）；解析不出的，
--      `case_id` 留空、进平台管理员工队列等人工。**两条分支的响应是同一个对象、同一组键**，
--      因为「收进来了」这件事与「认不认识这个 id」无关——这是 019 关成员资格探针用的同一招，
--      区别是这里连"未知"都不必拒收（DSA 不许把通知丢掉）。查询侧同理：未开案的报告在人工处理前
--      一律回 `received`，与案件刚开的 `open`/`triage` 映射到同一个词，所以拿着回执也探不出归属。
--
-- 刻意不做的两面，写清楚而不是留白：
--   * 没有来源地址维度的限流：本轮实测 API 看不到可信地址（`services/api/Dockerfile:13` 的 uvicorn
--     既无 --proxy-headers 也无 FORWARDED_ALLOW_IPS，`services/gateway/nginx.conf:26-28` 用
--     `$proxy_add_x_forwarded_for` 追加而非覆盖），所以这里只按"同一署名邮箱"与"全局每分钟"两个键计数，
--     它挡的是脚本化的洪水，不挡分布式投递——后者要先修可信地址，登记在同一张清单的已修第 24 条末尾。
--   * 本文件不新增任何"冻结"判据：下载/许可/付款/删除在 legal_hold 下各自拦在哪儿，是另一轮的事，
--     由 scripts/report_drill.py 逐条量出来再记账；这里只保证"投诉进得来、案件开得出、举报人不出卖"。

CREATE TABLE rights_reports (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  reference text NOT NULL UNIQUE,
  token_hash char(64) NOT NULL UNIQUE,
  subject_type text NOT NULL CHECK (subject_type IN ('project','candidate','asset','brand_brief','offer','user','other')),
  subject_id text NOT NULL,
  work_identification text NOT NULL,
  location text NOT NULL,
  grounds text NOT NULL CHECK (grounds IN ('copyright','voice_likeness','trademark','harassment','minor_safety','malware','other')),
  reporter_name text NOT NULL,
  reporter_email text NOT NULL,
  attested_statements text[] NOT NULL,
  case_id uuid REFERENCES moderation_cases(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT report_attestations_present
    CHECK (attested_statements @> ARRAY['good_faith','accuracy']::text[]),
  CONSTRAINT report_subject_id_shape CHECK (length(btrim(subject_id)) BETWEEN 1 AND 200),
  CONSTRAINT report_work_identification_present CHECK (length(btrim(work_identification)) BETWEEN 3 AND 2000),
  CONSTRAINT report_location_present CHECK (length(btrim(location)) BETWEEN 3 AND 2000),
  CONSTRAINT report_email_shape CHECK (reporter_email ~* '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$'),
  CONSTRAINT report_name_shape CHECK (length(btrim(reporter_name)) BETWEEN 1 AND 200)
);

CREATE INDEX rights_reports_unresolved ON rights_reports (created_at) WHERE case_id IS NULL;

-- 通知是证据，不是工单：落库之后一个字都不许改，一行都不许删。
-- 唯一的例外在下面 link_rights_report 需要的那一步：case_id 从 NULL 变成一个值，且只此一次。
-- 为什么在触发器而不是只靠 GRANT：music_admin 是 superuser（pg_roles 实读），对它有权限上的
-- 天然豁免，所以"改不动"这件事必须由表自己的约束说；GRANT 只负责 music_app 那一侧根本没有写权限。
CREATE FUNCTION guard_rights_report_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'a filed rights report cannot be deleted' USING ERRCODE='42501';
  END IF;
  IF NEW.reference IS DISTINCT FROM OLD.reference
     OR NEW.token_hash IS DISTINCT FROM OLD.token_hash
     OR NEW.subject_type IS DISTINCT FROM OLD.subject_type
     OR NEW.subject_id IS DISTINCT FROM OLD.subject_id
     OR NEW.work_identification IS DISTINCT FROM OLD.work_identification
     OR NEW.location IS DISTINCT FROM OLD.location
     OR NEW.grounds IS DISTINCT FROM OLD.grounds
     OR NEW.reporter_name IS DISTINCT FROM OLD.reporter_name
     OR NEW.reporter_email IS DISTINCT FROM OLD.reporter_email
     OR NEW.attested_statements IS DISTINCT FROM OLD.attested_statements
     OR NEW.created_at IS DISTINCT FROM OLD.created_at
     OR OLD.case_id IS NOT NULL THEN
    RAISE EXCEPTION 'a filed rights report is immutable (only case_id may be attached, once)'
      USING ERRCODE='42501';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER rights_reports_immutable BEFORE UPDATE OR DELETE ON rights_reports
  FOR EACH ROW EXECUTE FUNCTION guard_rights_report_immutable();

-- 归属解析：唯一一处「按 id 查对象」的地方，且它只在函数内部被调用，返回值不出现在任何响应里。
-- 非法 uuid 形状先被正则挡掉，不进入 ::uuid 转换——不是怕报错，是怕一次转换异常的形状被调用方读成
-- 「这个 id 不存在」，那正是本文件要关的口子。
CREATE FUNCTION report_subject_workspace(p_subject_type text, p_subject_id text) RETURNS uuid
LANGUAGE sql STABLE AS $$
  SELECT CASE p_subject_type
    WHEN 'project'     THEN (SELECT workspace_id FROM song_projects     WHERE id = p_subject_id::uuid)
    WHEN 'candidate'   THEN (SELECT workspace_id FROM audio_candidates  WHERE id = p_subject_id::uuid)
    WHEN 'asset'       THEN (SELECT workspace_id FROM media_assets      WHERE id = p_subject_id::uuid)
    WHEN 'brand_brief' THEN (SELECT workspace_id FROM brand_briefs      WHERE id = p_subject_id::uuid)
    WHEN 'offer'       THEN (SELECT workspace_id FROM asset_offers      WHERE id = p_subject_id::uuid)
    ELSE NULL
  END
  WHERE p_subject_id ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
$$;

CREATE FUNCTION file_rights_report(
    p_subject_type text, p_subject_id text, p_work text, p_location text, p_grounds text,
    p_name text, p_email text, p_attested text[], p_token_hash char(64))
  RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
  v_id uuid := gen_random_uuid();
  v_ref text;
  v_ws uuid;
  v_case uuid;
BEGIN
  IF p_subject_type IS NULL OR NOT (p_subject_type = ANY (
       ARRAY['project','candidate','asset','brand_brief','offer','user','other'])) THEN
    RAISE EXCEPTION 'unsupported subject type' USING ERRCODE='22023';
  END IF;
  IF p_grounds IS NULL OR NOT (p_grounds = ANY (
       ARRAY['copyright','voice_likeness','trademark','harassment','minor_safety','malware','other'])) THEN
    RAISE EXCEPTION 'unsupported grounds' USING ERRCODE='22023';
  END IF;
  IF p_token_hash IS NULL OR p_token_hash !~* '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'a receipt token is required' USING ERRCODE='22023';
  END IF;

  v_ws := report_subject_workspace(p_subject_type, p_subject_id);
  IF v_ws IS NOT NULL THEN
    -- 案件只带事实，不带举报人身份：opened_by 是这条通知自己的不可识别引用，
    -- evidence 里放的是回执编号与主张，租户侧 assets.py:232 的 SELECT * 看得见这些，看不见邮箱。
    INSERT INTO moderation_cases(workspace_id, subject_type, subject_id, case_type, severity, evidence, opened_by)
    VALUES (v_ws, p_subject_type, p_subject_id, p_grounds, 'medium',
            jsonb_build_object('report_reference', 'pending',
                               'report_id', v_id::text,
                               'grounds', p_grounds,
                               'location', p_location,
                               'work_identification', p_work),
            'report:' || v_id::text)
      RETURNING id INTO v_case;
  END IF;

  v_ref := 'RPT-' || upper(substr(translate(v_id::text, '-', ''), 1, 10));
  INSERT INTO rights_reports(id, reference, token_hash, subject_type, subject_id,
                             work_identification, location, grounds,
                             reporter_name, reporter_email, attested_statements, case_id)
  VALUES (v_id, v_ref, p_token_hash, p_subject_type, p_subject_id,
          p_work, p_location, p_grounds,
          p_name, lower(btrim(p_email)), p_attested, v_case);

  -- 两条分支到这里都写了同一张表、同一组列，返回值也只由这张表的字段构成：
  -- 一个调用者能观察到的差异，只有他自己写进去的那些值。
  RETURN jsonb_build_object('reference', v_ref, 'status', 'received',
                            'received_at', now(),
                            'next_step', 'a human reviews it; the receipt in your hands is the only way to ask about it');
END $$;

-- 凭回执查询：口令不出现在 URL 里（访问日志会留），所以比对的是 SHA-256。
CREATE FUNCTION rights_report_status(p_token_hash char(64)) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
  SELECT jsonb_build_object(
           'reference', r.reference,
           'status', CASE
             -- 没开案的、和刚开案的一律回 received：这是刻意的合并，见文件头第 3 条。
             WHEN r.case_id IS NULL THEN 'received'
             WHEN c.status IN ('open','triage') THEN 'received'
             ELSE c.status
           END,
           'grounds', r.grounds,
           'subject_type', r.subject_type,
           'received_at', r.created_at,
           'updated_at', COALESCE(c.updated_at, r.created_at),
           -- 说明理由只在人真的做过决定之后给，且只给案件上的限制事实，不给内部处置细节。
           'restrictions', CASE WHEN c.status IN ('open','triage') OR r.case_id IS NULL
                                THEN '{}'::jsonb ELSE COALESCE(c.restrictions,'{}'::jsonb) END
         )
    FROM rights_reports r
    LEFT JOIN moderation_cases c ON c.id = r.case_id
   WHERE r.token_hash = p_token_hash
$$;

-- 平台管理员工队列：含未开案的。PII 只从这一条出去，谓词写在函数里（RLS 对 superuser 拥有的
-- DEFINER 函数不构成任何约束，这一点上面 pg_roles 已经量过）。
CREATE FUNCTION rights_report_queue(p_actor uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE v_admin boolean; v_rows jsonb;
BEGIN
  SELECT coalesce(is_platform_admin,false) INTO v_admin FROM users WHERE id = p_actor;
  IF v_admin IS DISTINCT FROM true THEN
    RAISE EXCEPTION 'platform administrator required' USING ERRCODE='42501';
  END IF;
  -- SELECT ... INTO, not RETURN QUERY: this function returns one jsonb value, not a set. RETURN QUERY
  -- in a non-SETOF function is a creation-time error, and plpgsql only checks the body when it runs,
  -- so the first thing that noticed the earlier spelling was the drill calling it.
  SELECT coalesce(jsonb_agg(jsonb_build_object(
           'reference', r.reference,
           'subject_type', r.subject_type,
           'subject_id', r.subject_id,
           'grounds', r.grounds,
           'work_identification', r.work_identification,
           'location', r.location,
           'reporter_name', r.reporter_name,
           'reporter_email', r.reporter_email,
           'attested_statements', r.attested_statements,
           'case_id', r.case_id,
           'created_at', r.created_at)
         ORDER BY r.created_at DESC), '[]'::jsonb)
    INTO v_rows FROM rights_reports r;
  RETURN v_rows;
END $$;

-- 把一份未开案的报告挂到一条已有案件上（人身类投诉走这一条：员工先按现有通道开案，再关联）。
-- 只许关联一次：第二次会拒绝，因为「谁在等谁」必须只有一个答案，不留可改写的中间态。
CREATE FUNCTION link_rights_report(p_actor uuid, p_report uuid, p_case uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE v_admin boolean; v_row rights_reports%ROWTYPE;
BEGIN
  SELECT coalesce(is_platform_admin,false) INTO v_admin FROM users WHERE id = p_actor;
  IF v_admin IS DISTINCT FROM true THEN
    RAISE EXCEPTION 'platform administrator required' USING ERRCODE='42501';
  END IF;
  SELECT * INTO v_row FROM rights_reports WHERE id = p_report;
  IF NOT FOUND THEN RAISE EXCEPTION 'no such report' USING ERRCODE='P0002'; END IF;
  IF v_row.case_id IS NOT NULL THEN
    RAISE EXCEPTION 'a report is linked once: it already carries case %', v_row.case_id USING ERRCODE='23505';
  END IF;
  PERFORM 1 FROM moderation_cases WHERE id = p_case;
  IF NOT FOUND THEN RAISE EXCEPTION 'no such case' USING ERRCODE='P0002'; END IF;
  -- 绕过本表不可变触发器的唯一途径：这条函数不 UPDATE 通知的任何事实字段，它写的是关联，
  -- 而关联在表上被约束成单向（下面那条触发器函数只放过这一列的变更）。
  UPDATE rights_reports SET case_id = p_case WHERE id = p_report;
  RETURN jsonb_build_object('linked', true, 'reference', v_row.reference, 'case_id', p_case);
END $$;

REVOKE ALL ON FUNCTION guard_rights_report_immutable() FROM PUBLIC;
REVOKE ALL ON FUNCTION report_subject_workspace(text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION file_rights_report(text, text, text, text, text, text, text, text[], char(64)) FROM PUBLIC;
REVOKE ALL ON FUNCTION rights_report_status(char(64)) FROM PUBLIC;
REVOKE ALL ON FUNCTION rights_report_queue(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION link_rights_report(uuid, uuid, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION file_rights_report(text, text, text, text, text, text, text, text[], char(64)) TO music_app;
GRANT EXECUTE ON FUNCTION rights_report_status(char(64)) TO music_app;
GRANT EXECUTE ON FUNCTION rights_report_queue(uuid) TO music_app;
GRANT EXECUTE ON FUNCTION link_rights_report(uuid, uuid, uuid) TO music_app;
-- 表本身一个字都不授权给应用角色：读经上面三条函数，写只有 file_rights_report 一条路。
REVOKE ALL ON rights_reports FROM music_app;
