Feature: 从对话到可信 Master 的垂直切片
  Background:
    Given 一个已认证 Creator 属于 workspace A
    And 存在固定且不可变的 Provider Capability Snapshot

  Scenario: 用户修改并生成两个候选
    Given 项目当前 SongSpec revision 为 4
    And /lyrics 已锁定
    When 用户要求“保留歌词，编曲更克制，生成两版”
    Then Orchestrator 只提出不触碰 /lyrics 的 PatchCommand
    And 领域服务创建不可变 revision 5
    When 用户接受未过期的 GenerationQuote
    Then 系统冻结额度一次
    And 供应商只收到一次幂等请求
    And 两个候选完成媒体入库、解码校验和 sha256
    And 候选绑定 revision 5 与 Provider Snapshot

  Scenario: 选定 Master 后创建权利快照
    Given 候选 1 和候选 2 均为 ready
    When 用户确认候选 2 为 Master
    Then 系统创建 MasterSelection 和不可变 AssetSnapshot
    And 创建 RightsManifest version 1
    And 页面按钮由 rights capabilities 派生
    And 历史候选和旧 Master 不被删除
