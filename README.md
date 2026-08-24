# AstrBot 小黑盒适配器

> 将小黑盒 @、评论回复、帖子上下文与主动浏览接入 AstrBot 原生会话、人格和 Agent 链路。

[![CI](https://github.com/674537331/astrbot_plugin_xiaoheihe_adapter/actions/workflows/ci.yml/badge.svg)](https://github.com/674537331/astrbot_plugin_xiaoheihe_adapter/actions/workflows/ci.yml)
[![CodeQL](https://github.com/674537331/astrbot_plugin_xiaoheihe_adapter/actions/workflows/codeql.yml/badge.svg)](https://github.com/674537331/astrbot_plugin_xiaoheihe_adapter/actions/workflows/codeql.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

当前版本：**v1.3.3**

小黑盒通知会转换为 `AstrBotMessage`，通过 `commit_event()` 进入 AstrBot 原生事件队列。模型、人格、会话历史、记忆、Agent、MCP、Skills、Web Search 和已授权工具仍由 AstrBot 负责；本插件负责小黑盒平台接入、上下文构建、图片预处理、幂等发送与主动浏览。

## v1.3.3 重点

- 楼层上下文按局部相关性路由：普通短回复不新增一次额外 LLM 判断，正常讨论继续保留原帖；只有既有长楼层压缩明确判定已经 `drifted` 时才省略低相关原帖文字与原帖视觉，当前消息明确提到原帖、楼主或原帖图片时立即恢复原帖。
- 图片来源扩展为“当前评论 → 直接回复对象 → 楼层锚点 → 原帖”，近距离回复链图片优先占用视觉槽位，并继续执行所有者身份绑定、来源校验和失败降级。
- 长楼层压缩拥有独立超时预算，同一事件最多真正尝试一次压缩 Provider；失败后直接使用确定性上下文 fallback，不重复消耗慢公益站预算。无新增小黑盒 API、数据库迁移或运行依赖。

## v1.3.2 重点

- 收敛昵称、UID 与本地身份锚点在模型上下文中的重复展开：当前发送者完整身份继续由可信 `xiaoheihe_sender_identity` 持久绑定，临时 runtime/community 上下文只引用该绑定；身份仅用于发言归属和第一人称消歧，非必要时明确要求回复正文不要主动称呼、复述或评价身份值。
- 长楼层压缩继续使用本地 `speaker_n` 校验身份，但同一参与者的完整身份在相关背景中最多展开一次；直接回复对象保留原文与必要归属，已经在直接回复对象中绑定的身份不会在参与者锚点再次展开。图片压缩器同样禁止把图片所有者昵称、UID、角色或本地锚点写进视觉摘要。
- 修正真实分区时代码仍把主动事件描述为“主动浏览推荐流”的旧上下文文案，统一为来源无关的主动浏览表述；不新增小黑盒 API、数据库迁移或运行依赖，权限、审核、额度、发送与真实分区抓取语义保持不变。

完整历史见 [CHANGELOG.md](CHANGELOG.md)。

## 核心能力

- 将小黑盒 @、评论回复、帖子上下文和主动浏览接入 AstrBot 原生 Agent 链路；
- 通过帖子/楼层稳定 session 保留连续公开讨论；
- 保留真实 UID / 本地身份锚点，避免共享楼层把不同用户混成同一人；
- 支持文本模型、图片预处理、Web Search / MCP / Skills 等 AstrBot 原生能力；
- 主动浏览支持真实分区优先，并在真实分区全部失败时回退推荐流；
- 支持 dry-run、人工审核、无审核直发和发送幂等保护；
- 提供 AstrBot Plugin Page 管理账号、来源、配置、日志、事件与存储。

## 安装

推荐直接通过 AstrBot 插件市场安装。

手动安装时，将仓库目录放入 AstrBot 插件目录后重启或在管理页重载插件。

运行依赖由 `pyproject.toml` 声明：

```text
aiosqlite>=0.20.0,<1
httpx>=0.27.0,<1
qrcode[pil]>=8.0,<9
```

支持范围：

```text
AstrBot >=4.24.2,<5
Python >=3.12,<3.15
```

## 首次使用

安装后打开 AstrBot Dashboard 中的小黑盒插件页面：

1. 在“扫码登录”中选择账号并获取二维码；
2. 使用小黑盒 App 扫码确认；
3. 在“状态总览”确认账号认证正常；
4. 在“设置”页配置通知、Provider、权限、图片理解、主动浏览和发送模式；
5. 主动浏览来源请前往独立的“浏览来源”页设置真实分区与推荐流回退分类。

账号 Cookie / Token 保存在 AstrBot 插件数据目录，不写入插件配置和普通日志。

## 浏览来源

v1.3.x 支持两类来源：

### 真实分区

配置 `topic_ids` 后，插件直接访问小黑盒真实分区帖子流。

“浏览来源”页提供只读探测：

```text
真实分区目录
→ topic_id / 名称 / 分组
→ 小样本帖子流验证
```

最多可选择 20 个真实分区。运行时最多 4 路并发读取，跨分区帖子按 ID 去重后按创建时间/热度合并排序。

只要本轮至少一个真实分区成功，就只使用真实分区结果；只有全部真实分区都失败，才额外读取一次推荐流。

### 推荐流回退

未配置真实分区，或全部真实分区本轮失败时，插件读取：

```text
GET /bbs/app/feeds
```

然后根据帖子自带标签在本地匹配 `fallback_sources`。

因此“数码硬件”“PC 游戏”等只是兼容推荐流的本地过滤分类，不是小黑盒服务端真实分区参数。

## Provider 路由

主回复仍由 AstrBot Agent Runner 负责。

插件配置：

```text
providers.llm_provider_id
```

只决定小黑盒事件的首选主 Provider；AstrBot 自身全局 `fallback_chat_models` 继续负责后续主模型回退。

辅助任务可以使用：

```text
providers.image_provider_id
providers.context_provider_id
```

图片和长楼层压缩都有独立 timeout / fallback / cooldown 语义；辅助 Provider 失败不会直接阻断最终主回复。

## 图片处理

楼层图片按对话距离处理：

```text
当前评论图片
→ 直接回复对象图片
→ 楼层锚点图片
→ 原帖图片
```

插件会在主 Agent 构建前优先尝试把图片转成带来源的文字事实，并执行图片所有者归属校验。

当前评论和直接回复对象属于最近视觉来源；它们的图片 Provider 全部失败时，可以保留原图作为 AstrBot 原生视觉最后兜底。楼层锚点、原帖和未知来源仍保持低优先级受控降级。

只有图片所有者 UID 与当前发送者 UID 完全一致时，模型上下文才允许称为“你发的图片”。

## 楼层上下文

被动楼层的固定优先级：

```text
当前消息
> 当前消息直接回复对象
> 当前楼层锚点 / 最近对话
> 原帖背景
```

普通短回复继续使用有界确定性窗口，不为了判断是否歪楼额外请求模型。

长楼层达到既有压缩阈值后，插件复用上下文压缩结果判断：

```text
related / partial / unclear → 保留原帖

drifted                  → 本轮优先局部回复链并省略低相关原帖
```

判断失败、超时或不确定时保留原帖。用户重新明确提及原帖、楼主、帖子内容或原帖图片时，也会恢复原帖背景。

这个相关性状态只用于内部选择当前轮上下文，Bot 不会因此机械回复“你们已经歪楼了”。

## 身份与多人楼层

同一个公开楼层可以有多个用户连续回复。插件保留每轮发送者 UID；缺 UID 时使用事件级本地身份锚点。

完整当前发送者身份由可信 `xiaoheihe_sender_identity` 负责 Conversation 归属；临时帖子/楼层背景不会为增加模型注意力而重复展开同一个昵称和 UID。

昵称和 UID 的用途是：

```text
发言归属
第一人称消歧
必要的多人点名消歧
```

而不是要求模型每次回复都主动叫用户昵称。

## 主动浏览与发送

主动浏览处理链保持：

```text
帖子来源
→ 本地过滤 / 去重
→ AI
→ dry-run / 候选审核 / 直接发送
```

发送前后都有 outgoing 状态闸门。网络结果不确定时记录 `send_unknown`，先查询近期 Bot 评论确认，不盲目重复 POST。

## Plugin Page

当前页面：

```text
状态总览
扫码登录
设置
浏览来源
事件记录
主动审核
运行日志
存储管理
```

“浏览来源”的 `topic_ids` / `fallback_sources` 由独立页面管理；通用“设置”页不会重复编辑这些字段。

## 配置升级

v1.2.x 的旧：

```text
proactive_feed.source
```

在 v1.3.x 中迁移到：

```text
proactive_feed.fallback_sources
```

`source` 仍在原始 `_conf_schema.json` 中以隐藏字段保留，只用于升级迁移。

v1.3.3 不增加配置项、数据库迁移或运行依赖。

## 测试

本地：

```bash
python -m pip install -e ".[test]"
ruff check .
ruff format --check .
python -m compileall -q .
node --check pages/xiaoheihe/app.js
node --check pages/xiaoheihe/topic_probe.js
python tools/validate_repository.py
coverage run -m pytest -q
coverage report
```

v1.3.3 最终回归：

```text
287 passed
branch coverage: 83%
```

CI 还检查 AstrBot 4.24.2、4.26.2、当前支持范围内最新稳定版，以及 CodeQL、Dependency Review 和 Secret Scan。

## 文档

- [架构说明](docs/architecture.md)
- [AstrBot 兼容性](docs/compatibility.md)
- [测试说明](docs/testing.md)
- [小黑盒 API 契约](docs/xiaoheihe-api-contract.md)
- [第三方参考与许可](THIRD_PARTY_NOTICES.md)
- [更新日志](CHANGELOG.md)

## 安全提示

- 不要提交 Cookie、Token、二维码、设备 ID 或真实私人正文；
- 首次验证建议启用 dry-run；
- 修改真实评论发送链路时必须同时验证幂等与 `send_unknown`；
- 小黑盒相关接口是非公开客户端契约，平台更新后仍需要持续验证。

## License

MIT
