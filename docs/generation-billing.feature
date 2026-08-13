Feature: 生成、额度与成本一致性
  Scenario: 重复提交不会重复扣费
    Given 一个有效 quote 和 Idempotency-Key K
    When 客户端以 K 并发提交相同请求两次
    Then 只有一个 GenerationJob
    And 只有一个 Credit Hold 和一笔 hold 交易

  Scenario: 供应商永久失败
    Given 已冻结 20 credits
    When Provider 返回不可重试错误且无候选成功
    Then Job 状态最终为 failed 或 dead_letter
    And 对应 Credit Hold 被完整释放
    And 用户看到可理解失败原因

  Scenario: 部分成功
    Given 用户请求两个候选
    When 一个候选 ready 且另一个永久失败
    Then 系统执行显式的 partial_success_policy
    And 扣费、退款和候选数量对用户透明
    And 财务账本可与 provider cost 对账
