# 数据、AI 与质量治理

## AI 权限边界

模型可以：理解意图、提出 SongSpec Patch、解释变更、建议质量修复和偏好。

模型不能：写数据库、扣费、提交 Provider、选择 Master、声明版权、签发 License、改变 Rights、处理退款或切换总闸。

## 版本化事实

以下内容必须带版本和校验和：

- Prompt；
- Model；
- Tool Schema；
- Quality Ruleset；
- Provider Adapter；
- Capability Snapshot；
- Rights Policy；
- License Template。

历史生成和评估永远引用当时版本，不按当前配置重解释。

## 数据分类

- Public：公开 Offer/Brief 的最小展示字段；
- Internal：产品指标、模型配置、运行日志；
- Confidential：项目、歌词、音频、生成参数、合同和财务；
- Restricted：认证凭证、支付标识、声音授权、投诉证据和个人数据。

默认不把用户私有项目、完整歌词或音频发送给未批准的分析/训练系统。

## 人类贡献记录

系统记录直接编辑、Patch Diff、锁定、分支、评论、A/B 选择、Master 选择和上传材料。它用于来源追踪和争议处理，不自动保证版权成立。

## 质量治理

28 维引擎是确定性参考系统。上线后应将评分与真人结果校准：

- A/B 选择；
- Master 转化；
- 完播、下载、分享；
- 再次修改；
- 复购、授权；
- 退款、投诉和 Rights-ready。

模型或规则升级必须：离线 Golden Dataset、版本间差异、偏差检查、灰度、可回滚和线上指标保护。

## 用户控制

用户偏好模型应支持：查看、修改、关闭学习、删除和导出。Workspace 管理员应能决定团队数据是否用于个性化或产品改进。
