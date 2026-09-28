# 第二阶段实施计划 · 前端产品化深化

> 日期：2026-08-13 · 团队：软件开发团队（交付总监齐活林编排）
> 依据：`docs/CODE_WALKTHROUGH.md` §5.4–5.7 + §7 T05/T06；上一轮已把前端还原为可维护源码（app.js 1306 行）。

## 背景与边界

- 本机**无 Docker / 无浏览器**：本轮前端改动只能做 `node --check` + 结构走查，无法浏览器点验；E2E 仍是环境阻塞项，不在本轮范围。
- 数据已就绪：`generation_steps`（worker 写入 / `main.py:1018` 返回）、`ab_choice`/`candidate_played` 事件通道（`creation.py:214`）、28 维质量数据（`quality.py` 返回 `PAC/TSMI/NSRQ/C3AC` + 28 维 + `CriticalPenalty`）。
- 原则：**最小变更、纯前端为主、零第三方依赖（雷达图用原生 SVG）**；SSE 因同步 psycopg2 代码库存在事件循环阻塞风险，降为 P1 可选，优先用优化后的轮询渲染时间线。

## 任务分解（按优先级）

| 编号 | 任务 | 类型 | 验收标准 |
|---|---|---|---|
| **B1** | 任务生成步骤时间线 | 前端 | `generation_steps`（submit→ingest→settle）渲染为垂直 stepper，含状态/耗时/失败 error；任务完成自动刷新候选与余额；`waitJob` 保留但加超时友好提示 |
| **B2** | 候选 A/B 盲听对比 | 前端 | 候选并排卡片 + 盲听开关（隐藏来源只显 A/B）+ 播放进度/时长 + 一键设为 Master + 回写 `ab_choice` 事件 |
| **B3** | 28 维质量可视化 | 前端 | 单个分数 + 28 格 div 升级为原生 SVG 雷达图 + 分组（Core/Important/Auxiliary）+ hover 解释 + `CriticalPenalty` 风险高亮 |
| **B4** | 列表分页 + 搜索 + 状态筛选 | 前端(+后端) | 项目/资产/订单/许可/工单列表接上轮 `limit/offset`，加分页控件/搜索框/状态 Tab；后端确认并补齐 `total` 计数返回 |
| **B5** | 任务实时化（SSE） | 后端(可选 P1) | 新增 `/api/jobs/{id}/events` SSE 端点，EventSource 订阅，失败回退轮询；**若事件循环阻塞风险不可控则保持轮询并标注** |

## 依赖关系

```
B1（时间线）─┐
B2（A/B）    ├─ 均基于现有候选/任务数据，可并行，无强依赖
B3（质量）   │
B4（分页）───┴─ 依赖上一轮后端 limit/offset（已就绪）
B5（SSE）    ─ 依赖 B1 结论，可后置/可选
```

## 验收与验证（无 Docker，尽力而为）

```bash
cd /Volumes/Extra/CodeProj/web3_music
find services -name '*.js' -print0 | xargs -0 -n1 node --check
/Users/panhao/.workbuddy/binaries/python/envs/web3music/bin/python -m compileall -q services
/Users/panhao/.workbuddy/binaries/python/envs/web3music/bin/python -m pytest -q tests/unit
/Users/panhao/.workbuddy/binaries/python/envs/web3music/bin/python scripts/architecture-audit.py
```

- 前端：`node --check` 全绿 + 结构走查（新组件函数是否被挂载、事件通道是否对齐后端 event 类型）。
- QA 补测：B3 质量分组/渲染纯函数、B2 事件构造、B4 分页参数处理（若可单测）。

## 明确不做（本轮）

- 不引入 Vite/React（前端保持 beautify 后的原生 JS，降低风险）。
- 不做真实浏览器 E2E、不做 Docker 跨容器验证（环境阻塞）。
- 不重构 admin_v12 控制面（P1 后续）。
- 不接真实音乐供应商（商业动作，非代码）。
