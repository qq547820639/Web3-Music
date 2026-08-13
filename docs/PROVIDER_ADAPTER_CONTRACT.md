# 音乐 Provider Adapter 合同

## 目标

任何真实音乐 Provider 都必须在不改变领域层、账本或资产规则的前提下替换 Emulator。Adapter 只翻译供应商协议，不决定平台权利和财务事实。

## 必需能力

```text
get_capabilities() -> CapabilitySnapshot
quote(request) -> ProviderQuote
submit(request, idempotency_key) -> ProviderJob
get_status(provider_job_id) -> NormalizedResult
cancel(provider_job_id) -> CancelResult
health_check() -> Health
reconcile_cost(result, ready_count) -> Cost
verify_webhook(raw, headers) -> VerifiedEvent   # 供应商支持回调时
```

## Capability Snapshot 必需字段

- provider、adapter_version、environment；
- approval_status、contract_version、captured_at；
- model/version；
- candidate_count_min/max；
- custom lyrics、styles、instrumental、cancel、webhook 支持；
- 参数限制和内容限制；
- 预计延迟、计费单位和失败扣费规则；
- 输出 URL 生命周期和媒体下载权限；
- commercial_rights、distribution、sublicense、content_id 等权利状态；
- 数据保留、训练使用和隐私条款摘要。

Snapshot 在 Quote 创建时冻结。后续条款变化只影响新 Quote。

## Submit 合同

- 必须接受稳定 Idempotency-Key；
- 相同 Key + 相同请求返回同一 Provider Job；
- 相同 Key + 不同请求返回冲突；
- 平台 Job ID 应作为 Provider 侧参考；
- 结果不确定时不可自动创建第二个 Provider Job；
- Adapter 必须区分“明确未提交”和“提交结果未知”。

## Normalized Result

```json
{
  "status": "queued|processing|completed|partial|failed|cancelled",
  "provider_job_id": "...",
  "candidates": [
    {
      "provider_clip_id": "...",
      "status": "completed|failed",
      "audio_url": "https://...",
      "title": "...",
      "duration": 123.4,
      "metadata": {}
    }
  ],
  "raw": {},
  "provider_cost": 0
}
```

Provider 返回的每个字段都需要验证。候选缺失必须显式转换为失败候选。

## Contract Test 必过场景

- Capability 完整；
- Submit 幂等和冲突；
- Success、Partial Success、Failed、Timeout、429 和 5xx；
- Cancel；
- Poll 恢复；
- 重复/乱序 Webhook；
- URL 过期和媒体下载失败；
- 候选数少于承诺；
- 成本对账；
- 合同和权利 Snapshot 固定。

随包脚本：

```bash
./scripts/contract-test.sh
```

## 商业批准条件

Provider 只有在以下证据齐全时才能被配置为 `approved_commercial`：

- 书面平台/API 使用权；
- 允许向终端用户提供服务；
- 明确输出下载、存储、播放、商业使用和许可边界；
- 明确输入数据和输出用于训练的条款；
- 成本、退款、SLA、速率限制和终止条款；
- 法务和管理层签字；
- 真实 Contract Test 和至少 100 次回归通过。
