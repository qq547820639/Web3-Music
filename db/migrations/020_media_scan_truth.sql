-- 020_media_scan_truth.sql
-- `media_assets.scan_status` 记录的是一个当时没有人生产的事实。这一条把它改成真的，并把
-- 「说 clean 就必须交出出处」从评审意见搬进修状态触发器。
--
-- 读到的形状（不是推测，逐处带位置）：
--   * `db/migrations/001_production_candidate.sql:294` 定义
--     `scan_status text NOT NULL CHECK(scan_status IN ('pending','clean','rejected'))`；
--   * `services/worker/worker.py:250` 在 PutObject 的 Metadata 里写 `{"scan-status": "clean"}`，
--     同文件 :253 的 INSERT 把 `scan_status` 字面写成 `'clean'`；全仓没有第二个写入点，也没有
--     任何扫描器（`SECURITY.md:23` 自己就写着「未集成独立反病毒……」）；
--   * 而这个值同时是出流接口的判据：`services/api/app/main.py:966` 对 `scan_status != 'clean'`
--     直接 404。
-- 于是这列给读者的承诺是「这份字节被扫过且干净」，机器实际给出的却是「代码走到过那一行」。
-- 更糟的是它带着一个读者：接口按它决定能不能取到音频，所以它不是惰性文档，是一扇永远绿着的门。
--
-- 为什么不是「干脆接一个引擎」：本机实测 Docker VM 只有 `4 cpus / 5.77 GiB`
-- （`docker info`：MemTotal=6198632448），而 ClamAV 官方文档
-- （https://docs.clamav.net/ ，2026-09-27 打开）写的最低推荐内存是「3 GiB+」、磁盘 5 GiB 空闲；
-- `docker pull clamav/clamav:1.4` 在这台 arm64 宿主上直接回答
-- `no matching manifest for linux/arm64/v8`，未检索到官方 arm64 镜像。把 3 GiB 起的常驻体
-- 塞进一个还要跑 Postgres/MinIO/api/两个 worker/两个模拟器/三份 nginx 并且要跑 20 步认证链的
-- 4 vCPU VM，代价不是口味问题。YARA 是规则引擎、没有可信的音频恶意签名典可指；厂商 API 要在扫描
-- 时刻外呼，与「compose 内闭环可认证」的前提冲突。所以本轮做的是**边界与真值**，引擎留作可插拔项。
--
-- 这一条之后：
--   * 域收敛为两个真能表达的状态：`unscanned`（这台部署没配引擎，事实就是没扫）与
--     `clean`（某个引擎在某个时刻对这份 sha256 给出的判决，出处记在同行的
--     `scan_engine`/`scan_detail`/`scanned_at`）。
--   * `'pending'` 与 `'rejected'` 被移出允许集，理由写在这里而不是留在注释习惯里：本表每一行的
--     `bucket`/`object_key`/`bytes`（`bytes>0`）都是 NOT NULL，也就是「对象已经在桶里」是这张表的
--     存在前提；被扫出来的脏字节从不上传（`services/worker/worker.py` 的 `scan_media` 在
--     `store_media` 之前开火，判决为 infected 时抛错、字节不进桶、也不进本表），所以这两态在这张表
--     里根本不可表示，留着就是第二个装饰状态。拒绝记在 `audio_candidates.metadata.ingest_error`
--     与作业终态上，那里才是它能被说清楚的地方。
--   * `media_assets_scan_proof` 触发器让「写了 clean 却交不出出处」在数据库层面不可能，
--     而不是靠人记得；同一触发器要求 `unscanned` 行不得藏着判决残留。
--   * 存量行一次性改口：本轮之前所有 `'clean'` 都是字面量写的，一律降为 `'unscanned'`。
--     出流接口不因此变红——`main.py` 里那句 `scan_status != 'clean'` 判据的移除是配套的，
--     访问控制本来就在签名 token 与工作区上（`stream_media` 的 `verify_media_token` 与
--     `workspace_id=%s`），而「脏字节进不了表」由写入侧与触发器保证。
--
-- 迁移由 `music_admin` 执行（`docker-compose.yml` 的 migrate 服务，镜像里该角色是超级用户），
-- 所以它能 ALTER 这张 RLS 表；本文件不放开任何 DML 权限，`music_app` 仍然只有 SELECT。

ALTER TABLE media_assets DROP CONSTRAINT media_assets_scan_status_check;

-- 域只留可表示的两态。'rejected' 想回来，必须先改掉 bucket/object_key/bytes 的 NOT NULL，
-- 而那等于把「已入库对象」这张表变成两处语义。
ALTER TABLE media_assets
  ADD CONSTRAINT media_assets_scan_status_check
  CHECK (scan_status IN ('unscanned', 'clean'));

ALTER TABLE media_assets
  ADD COLUMN scan_engine text,
  ADD COLUMN scan_detail text,
  ADD COLUMN scanned_at timestamptz;

-- 出处三列只准与状态同时出现：要么整组都有（clean），要么整组为空（unscanned）。
-- 少了这一道，将来任何人把状态从 clean 改回 unscanned 都会留下一条指向不存在判决的出处，
-- 那正是本条迁移在消灭的东西。
CREATE OR REPLACE FUNCTION media_assets_scan_proof() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.scan_status = 'clean' THEN
    IF NEW.scan_engine IS NULL OR length(trim(NEW.scan_engine)) = 0 THEN
      RAISE 'a clean media asset must name the engine that cleared it'
        USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.scanned_at IS NULL THEN
      RAISE 'a clean media asset must say when it was scanned'
        USING ERRCODE = 'check_violation';
    END IF;
  ELSIF NEW.scan_status = 'unscanned' THEN
    IF NEW.scan_engine IS NOT NULL OR NEW.scan_detail IS NOT NULL OR NEW.scanned_at IS NOT NULL THEN
      RAISE 'an unscanned media asset cannot carry scan provenance'
        USING ERRCODE = 'check_violation';
    END IF;
  END IF;
  RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS media_assets_scan_proof ON media_assets;
CREATE TRIGGER media_assets_scan_proof
  BEFORE INSERT OR UPDATE OF scan_status, scan_engine, scan_detail, scanned_at ON media_assets
  FOR EACH ROW EXECUTE FUNCTION media_assets_scan_proof();

-- 存量改口：这些行从来没有被扫过，之前那格 'clean' 是代码写的字面量。
-- 顺序上触发器先建、回填在后，这不需要例外：回填写的是 `unscanned` 且出处三列本来就是 NULL，
-- 正好是触发器要求 unscanned 行的形状。（反过来若先回填再建触发器，结论也一样——记在这里是为了
-- 让下次读到这段的人不必自己推。）
UPDATE media_assets SET scan_status = 'unscanned' WHERE scan_status = 'clean' AND scan_engine IS NULL;

COMMENT ON COLUMN media_assets.scan_status IS
  'unscanned = 这台部署没有配置扫描引擎，事实就是没扫；clean = scan_engine 在 scanned_at 对这份 sha256 给出的判决。被判定为脏的字节从不写入本表（写入侧在上传之前拒绝）。';
COMMENT ON COLUMN media_assets.scan_engine IS
  '给出 clean 判决的引擎自报名（例如 clamd <version> / database <CVD 版本>）；unscanned 行必须为 NULL。';
