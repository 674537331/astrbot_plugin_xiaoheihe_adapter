# AstrBot 兼容性说明（v1.3.2）

## 支持范围

插件声明：

```text
AstrBot >=4.24.2,<5
Python 3.12–3.14
```

CI 当前同时检查：

- 最低支持版本 AstrBot 4.24.2；
- 重点兼容版本 AstrBot 4.26.2；
- 当前支持范围内的最新稳定 AstrBot 包。

v1.3.2 只收敛插件送入 Agent 的身份上下文表达并修正主动浏览来源描述，不引入新的 AstrBot 核心 API、小黑盒 API、数据库迁移、配置项或运行依赖，也不改变 QQ 或其他平台行为。

## 使用的 AstrBot 能力

| 能力 | 使用方式 | 兼容性边界 |
| --- | --- | --- |
| 平台注册 | `register_platform_adapter` + `PlatformMetadata` | 注册 `xiaoheihe` 原生平台类型 |
| 入站消息 | `AstrBotMessage`、`MessageMember`、`Plain/Image` | 使用稳定消息 ID、session、group、sender 和 raw metadata |
| 事件提交 | `Platform.commit_event()` | 进入 AstrBot 原生事件队列 |
| 回复事件 | `AstrMessageEvent` 子类 | 只在最终文本聚合完成后提交小黑盒评论 |
| Agent 生命周期 | `on_agent_begin` / `on_agent_done` / `on_llm_response` | 区分工具状态、中间文本与最终答案 |
| 图片预处理 | `on_waiting_llm_request` + Provider API | 主 Agent 构建前完成图片转述/隔离 |
| 用户侧临时背景 | `on_llm_request` + `extra_user_content_parts` / `mark_as_temp()` | 帖子/楼层动态背景不重复写入 Conversation |
| 发送者身份 | 非临时 `extra_user_content_parts` | 每轮完整昵称 + UID / 备用锚点只由可信 sender binding 持久化；临时背景引用该绑定 |
| Provider 选择 | `selected_provider` + `Context.get_provider_by_id()` | 插件主模型仍进入 AstrBot 原生 Agent；回退列表由 AstrBot 全局配置决定 |
| Plugin Page | `window.AstrBotPluginPage` + `context.register_web_api()` + `astrbot.api.web` | Dashboard 认证后访问插件管理 API |
| 配置 | `AstrBotConfig` + `save_config()` + `_conf_schema.json` | Plugin Page 与原生插件设置共享配置对象 |
| 数据目录 | `StarTools.get_data_dir()` | SQLite、凭证和日志保留在插件专属数据目录 |
| 生命周期 | `initialize()` / `terminate()` / `Platform.terminate()` | 热重载重建适配器；关闭任务、HTTP Client、SQLite、SSE |

## v1.3.x 配置兼容性

### 为什么真实分区字段必须写进 `_conf_schema.json`

AstrBot 会按插件 schema 为配置补默认值并进行结构归一化，因此仅把新字段放进插件内部 `DEFAULT_CONFIG` 不足以保证持久化。

v1.3.0 将以下字段同时保留在原始 `_conf_schema.json`：

```text
proactive_feed.source
proactive_feed.topic_ids
proactive_feed.fallback_sources
```

三者都标记为 `invisible: true`：

- `source`：只保留给 v1.2.x 升级迁移；
- `topic_ids`：真实分区 ID 列表；
- `fallback_sources`：多选推荐流回退分类。

Plugin Page 的 `ConfigService.ui_schema()` 会把三者从通用设置表单中剔除，因此用户只在独立“浏览来源”页面维护来源配置。

### 旧 `source` 升级

旧版可能保存：

```text
source = hardware / game / pc_game / ...
```

新版本保存：

```text
fallback_sources = ["digital_tech", "pc_game", ...]
```

特殊升级场景是 AstrBot 在插件代码运行前已经根据新 schema 补入：

```json
{"fallback_sources": ["all"]}
```

而旧 `source` 仍然存在。迁移器会把这个 schema 默认值视为“尚未迁移”，优先保留旧非默认分类。若配置中已经存在新的非默认 `fallback_sources`，则新配置优先，不会被残留旧字段覆盖。

v1.3.2 不修改任何配置字段或默认值。

## Plugin Page 兼容性

v1.3.x 页面标签：

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

“浏览来源”独立调用：

- `GET feed/topics/probe`：真实分区目录与样例帖子只读探测；
- `GET config` / `POST config/save`：保存 `topic_ids` / `fallback_sources`；
- 不直接调用评论写接口。

旧的单选来源不会在通用设置表单再次出现。

## 主模型与回退

`providers.llm_provider_id` 只决定小黑盒事件的首选 Provider。AstrBot 4.x 的后续主对话回退仍来自全局 `fallback_chat_models`；插件不会为小黑盒单独重写全局列表。

如果需要严格得到：

```text
插件固定模型 → AstrBot 主模型 → 其他回退模型
```

应把 AstrBot 主模型放在全局回退列表的第一项。插件只记录期望链与实际链差异，不修改其他平台共享配置。

## 图片兼容性

小黑盒图片在 `on_waiting_llm_request` 阶段先从事件消息链隔离，再依次尝试：

```text
插件图片 Provider
→ AstrBot 默认图片转述 Provider
→ 当前会话 / 配置主模型
```

这样即使最终主模型只支持文本，也不会收到未经处理的 `Image` 内容类型。所有候选失败时只向最终 Agent 提供失败说明，不把原图重新塞给纯文本模型。

v1.3.2 只增加图片身份“归属而非称呼”的语义约束：所有者 UID 匹配、缺 UID 本地身份锚点、来源错位 fail-closed 和视觉缓存键均不变；图片压缩 Provider 被明确要求不要把所有者昵称、UID、角色或本地锚点写进视觉摘要。

普通 `grok_web_search` 不接收已隔离的事件原图；只有明确搜图/识图意图才在工具执行期间临时恢复有界引用。

## 会话兼容性

同一楼层共享 `xhh_thread_<post_id>_<root_comment_id>`，以保留公开讨论连续性。由于 AstrBot 的普通 Conversation 历史本身不保存平台 sender 字段，插件把当前发送者昵称和 UID / 备用锚点作为非临时 `xiaoheihe_sender_identity` 内容块加入该轮历史。

v1.3.2 保留这一持久 sender binding，但不再在临时 runtime/community 背景重复展开同一当前发送者身份。原帖、最近楼层和直接回复对象仍按需要保留来源归属；长楼层压缩中的完整参与者身份采用“一处绑定、后续 `speaker_n` 引用”的形式。动态帖子正文、楼层、图片描述和焦点信息仍使用临时内容，只参与当前请求。

## v1.3.2 对现有数据的影响

- **无数据库迁移**：数据库版本继续沿用现有 v10；
- **无新增运行依赖**：仍为 `aiosqlite`、`httpx`、`qrcode[pil]` 等既有依赖；
- **无配置迁移**：`topic_ids`、`fallback_sources`、Provider 与主动发送配置不变；
- **账号凭证不变**：仍从插件数据目录读取；
- **通知游标不变**：升级不会重置 mention/reply `message_id` 边界；
- **会话 ID 不变**：不会因身份上下文收敛拆分既有 Conversation；
- **主动候选/发送闸门不变**：身份提示调整不绕过审核与幂等逻辑。

## 兼容性验证策略

CI 分成两类：

1. **核心质量任务**：仓库结构、JSON/YAML、Ruff、格式、Python 编译、前端 JavaScript、AstrBot stub import、pytest + branch coverage；
2. **真实 AstrBot 包契约**：安装最低版本、重点版本和最新稳定版，检查插件依赖的核心文件/符号。

v1.3.2 核心回归为 280 项、总覆盖率 83%，并通过 AstrBot 4.24.2、4.26.2 与当前支持范围内最新稳定版的契约检查。另有 CodeQL、Dependency Review 和 Secret Scan。发布版本一致性由 `tools/validate_repository.py` 强制校验，避免 README、CHANGELOG、元数据和运行时诊断再次出现版本漂移。

历史兼容性演进请查看 [CHANGELOG.md](../CHANGELOG.md)。
