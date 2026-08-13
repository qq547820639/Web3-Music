Feature: 权利能力和政策控制
  Scenario: 商业能力未知
    Given RightsManifest 的 commercial_use 为 unknown
    Then stream 可按策略允许
    But license、sublicense、distribute、mint、content_id 被阻断

  @pending
  Scenario: 争议资产进入 Legal Hold
    Given 一个已验证资产
    When 合法投诉被受理
    Then 新 RightsManifest 状态为 legal_hold
    And 下载、许可、付款和删除按政策冻结
    And 原始证据不可被普通管理员修改

  @pending
  Scenario: 未经授权的声音克隆
    Given 上传音频可识别为第三方声音
    And 没有有效同意证据
    When 用户请求生成
    Then PolicyDecision 为 block
    And 不创建 provider job 或额度冻结
