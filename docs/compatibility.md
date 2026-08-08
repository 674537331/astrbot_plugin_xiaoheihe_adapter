# AstrBot 兼容性说明（v1.2.13）

## 调查范围

项目面向 AstrBot `>=4.24.2,<5`，重点兼容 4.26.2；接收与真实评论链路的最新用户复测日志来自
AstrBot 4.27.2。v1.2.13 在 v1.2.12 焦点路由上增加被动长楼层的来源感知语义压缩和图片来源
路由：当前消息/直接回复对象保持原文，原帖与楼层分开压缩，被动楼层的原帖原图在最终 Agent
前强制转为硬限长描述或 fail-closed，主动浏览仍以原帖为主要话题。
该版本复用已有的 Provider 与 `on_llm_request` 接口，没有新增 AstrBot API 依赖。2026-08-08
发布 v1.2.13 前再次核对：

- AstrBot 4.26.2 标签对应源码快照（提交 `a619988d2d181c884f7bf04e24f30c0ea0928ff6`）；
- AstrBot 4.26.8 标签中的 `Platform`、平台管理器、注册器、消息和事件源码；
- PyPI 已发布的最低版本 4.24.2、重点版本 4.26.2，以及 2026-08-08 的当前稳定版
  4.27.2 wheel；
- AstrBot 当前官方插件开发、平台适配器和 Plugin Pages 文档；
- `Platform`、`PlatformMetadata`、`register_platform_adapter`、`AstrBotMessage`、
  `AstrMessageEvent`、`MessageSession`、`commit_event()`、`send_by_session()`；
- `AstrBotConfig.save_config()`、`StarTools.get_data_dir()`、插件 `terminate()`；
- `Image` 消息组件、`on_agent_begin`、`on_agent_done`、`on_using_llm_tool`、
  `on_llm_tool_respond`、`on_llm_request`、`AstrMessageEvent.get_sender_id()`、
  `extra_user_content_parts` 与 `TextPart.mark_as_temp()`；
- Plugin Page 的 `window.AstrBotPluginPage`、`context.register_web_api()` 和
  `astrbot.api.web`。

本仓库以插件形式引用 AstrBot 运行时 API；单元测试使用仅覆盖接口形状的本地桩。CI 另从
PyPI 获取最低版本与重点版本包，核验实际 API 文件和所需符号。

## 采用的接口

| 能力 | 采用方式 | 兼容性结论 |
| --- | --- | --- |
| 适配器注册 | `register_platform_adapter(..., default_config_tmpl=..., adapter_display_name=..., logo_path="../logo.png", support_streaming_message=False)` | 让“机器人 → 新增适配器”显示“小黑盒”及仓库根目录图标；平台配置只含 `id/enable/profile_id` |
| 适配器构造 | `Platform(platform_config, event_queue)`；适配器构造函数接收 `platform_config/platform_settings/event_queue` | 与 4.26.2 的平台管理器调用方式一致 |
| 元数据 | `PlatformMetadata` | 声明内部名、实例 ID、展示名、默认配置和非流式能力 |
| 入站消息 | `AstrBotMessage` + `MessageMember` + `Plain/Image` | 设置稳定消息 ID、会话、群组、发送者、自身 UID 和结构化 `raw_message` |
| 事件 | `XiaoheiheMessageEvent(AstrMessageEvent)` | `send()` 完成平台处理后调用父类 `send()` |
| 多人楼层身份 | `MessageMember.user_id` + `AstrMessageEvent.get_sender_id()` + 非临时 `extra_user_content_parts` | session 仍按楼层共享；每轮真实 UID 随 `role=user` 历史持久化，避免不同参与者被压成同一匿名用户 |
| Agent 回复聚合 | `on_agent_begin()` + `on_agent_done()` + `on_llm_response()` + `AstrMessageEvent.get_result()` | 无工具的普通回复直接完成；有工具时忽略 `tool_call` 控制消息、暂存中间文本，最终只提交一条小黑盒评论 |
| Grok 带图查询兼容 | `on_using_llm_tool()` + `on_llm_tool_respond()` + `AstrMessageEvent.get_messages()` | 仅 `xiaoheihe + grok_web_search` 普通网页查询临时隔离顶层 `Image` 并恢复；明确搜图和其他工具保持原行为 |
| 分段/流式回复 | 非流式平台 `send()` + `send_streaming()` | 分段清理前恢复完整文本，任意数量的分段或流式片段只提交一条小黑盒评论 |
| 事件提交 | `Platform.commit_event(event)` | 进入 AstrBot 原生事件队列；模型调用由 AstrBot 核心负责 |
| 固定 LLM Provider | 事件入队前设置 `selected_provider` extra | 兼容 4.24.2 与 4.26.2 的主 Agent Provider 选择时序；留空时继续使用会话或全局 Provider |
| 上下文压缩 Provider | `Context.get_provider_by_id()` / `get_using_provider()` + `Provider.text_chat(persist=False)` | 仅长被动楼层额外调用；独立 Provider 留空时复用固定 LLM 或会话 Provider，任何异常回退 v1.2.12 临时背景 |
| 主动发送 | `send_by_session(MessageSession, MessageChain)` | 从确定性 session ID 恢复路由；失败时抛出明确错误 |
| 唤醒 | `event.is_wake` 和 `event.is_at_or_wake_command` | @ 与直接回复不依赖正文仍保留 `@昵称` |
| 用户侧上下文 | `filter.on_llm_request()` + `request.extra_user_content_parts` | 发送者 UID 身份块保持非临时并随本轮历史保存；动态帖子/楼层背景保持临时；长被动楼层可先按来源语义压缩，最终焦点仍按“当前消息 → 直接回复对象 → 最近楼层 → 原帖” |
| 图片 | `astrbot.api.message_components.Image(file=url, url=url)` | v1.0.0 只传经过安全校验的公开 HTTPS URL，交给 AstrBot 媒体链路 |
| 被动楼层图片预处理 | `Context.get_provider_by_id()` / `get_using_provider()` + `Provider.text_chat(persist=False)` | 当前评论图/原帖图分组处理，按固定图片 → 固定 LLM → 当前会话逐级尝试；所有尝试共享图片额外回复宽限且单事件硬限 60 秒，预算耗尽立即降级；原帖全部失败时移除原图，当前评论图失败时才保留原生视觉兜底 |
| 视觉降级 | `Context.get_provider_by_id()` / `get_using_provider()` + provider `modalities` | 被动楼层明确声明纯文本的 Provider 不参与图片预处理；其他事件沿用主 Provider 能力检查，明确不含 `image` 时移除图片并保留文本 |
| 配置 | 构造参数中的 `AstrBotConfig` + `save_config()` | 原生设置和 Plugin Page 共用同一个对象，不写核心配置文件 |
| 数据目录 | `StarTools.get_data_dir(plugin_name)` | 凭证与 SQLite 位于插件专属数据目录，覆盖更新后继续读取 |
| Plugin Page | `window.AstrBotPluginPage` + `context.register_web_api()` + `astrbot.api.web` | 4.26.2 完整可用；页面只通过受限 bridge 通信 |
| 配置表单 | `_conf_schema.json` + Plugin Page `config/schema` | 表单元数据与 AstrBot 原生插件设置共用同一 schema；配置值始终来自同一个 `AstrBotConfig` |
| 生命周期 | `Star.initialize()`、`Star.terminate()`、`Platform.terminate()` | 热重载时协调平台实例；停止时取消任务并关闭 HTTP Client/SQLite/SSE |

AstrBot 4.24.2、4.26.2 和 4.26.8 均在插件加载完成后调用可选的 `Star.initialize()`，并由
`Context.platform_manager` 暴露当前平台实例。AstrBot 覆盖更新会重新加载插件模块，但不会
替插件重建已经终止的平台实例。v1.1.1 在 `initialize()` 中识别已经处于运行阶段的平台
管理器，并调用当前源码提供的 `PlatformManager.reload(config)` 重建所有已启用的
`xiaoheihe` 实例；冷启动时实例列表为空，平台仍由 AstrBot 的正常初始化流程创建。

AstrBot 4.24.2–4.27.2 的本地 Agent 在工具执行前可通过独立 `event.send()` 输出工具状态，
`RespondStage` 在全局“分段回复”开启时也会按组件多次调用平台事件的 `send()`，即使平台元数据
声明 `support_streaming_message=False`。v1.2.9 使用 `on_agent_begin/on_agent_done` 区分 Agent
中间输出和最终输出：`tool_call`、推理与分段控制消息不会完成小黑盒事件；无工具调用时同样由
Agent 完成信号放行普通最终回复。最终发送仍优先恢复 `on_llm_response(priority=-1000)` 保存的
完整模型文本，缺失时回退同一事件的完整 `MessageEventResult.chain`。插件直接业务结果若发生在
继续调用 LLM 之前，会暂存、去重并放在最终模型回复之后，确保小黑盒字符截断时优先保留最终答案。

`grok_web_search` 会从 `AstrMessageEvent.get_messages()` 再次自动提取顶层图片，即使主 Agent 已经
通过固定图片 Provider 得到了图片文字描述。v1.2.10 在普通网页查询调用前暂时移出这些顶层
`Image`，调用完成后按原位置恢复；明确包含搜图/识图意图的查询不做隔离，显式 `image_urls`
仍由 Grok 工具自身处理。查询约束也只在工具名实际匹配 `grok_web_search` 时追加。该兼容逻辑
不修改 Grok 插件配置；没有安装或没有调用 Grok、使用其他工具及 QQ 等其他平台均保持原行为。

AstrBot 的 Conversation 以 `unified_msg_origin` 作为会话键，而本适配器的楼层 UMO 只包含
`xhh_thread_<post_id>_<root_comment_id>`，因此同楼层不同 UID 会共享上下文。AstrBot 的历史
`UserMessageSegment` 本身不保存平台 sender 字段。v1.2.11 不改变这一会话模型，而是在小黑盒
`on_llm_request` 中把 `event.get_sender_id()` 生成的短 UID 身份块作为普通（非 temp）
`extra_user_content_parts` 追加到每轮用户输入；按 AstrBot 4.24+ 的官方语义，普通内容块会进入
会话历史，只有 `mark_as_temp()` 才不持久化。QQ 和其他平台在钩子入口即返回，不添加该身份块。

v1.2.12 继续使用同一个 `on_llm_request` 接口，没有修改 AstrBot 的 Conversation 或 system
prompt。对于带 `root_comment_id` 的小黑盒被动回复，动态临时背景按“当前原生用户消息 → 直接
回复对象 → 最近楼层 → 原帖”排序；默认把原帖正文限制为 1600 字、普通最近楼层限制为 12 条，
每条普通楼层最多 800 字，并把通知中明确的直接回复对象从普通窗口中独立保留。当前消息会在
临时背景末尾出现一个仅用于模型定位的副本，但只有原生用户消息进入会话历史。主动浏览不应用
这些被动预算，继续使用全局原帖/评论上限并明确把原帖作为主要话题。因此改动只改变本插件提交
给现有 AstrBot API 的临时内容，不增加运行时契约。

v1.2.13 仍使用同一接口，但将 v1.2.12 的 1600 字 / 12 条背景降为短上下文和故障兜底。超过
默认 2400 字的被动楼层先通过 `Provider.text_chat(persist=False)` 生成分离的原帖摘要、楼层摘要
和局部话题关系；输入与输出均由插件本地代码设置硬上限，当前消息和直接回复对象不被摘要替换。
上下文 Provider 缺失、超时、异常或返回无效 JSON 时直接回退 v1.2.12 临时背景，不改变 AstrBot
Agent 调用。图片预处理同样复用 v1.2.2 起已有的 `Provider.text_chat()` 能力，但被动楼层不再
要求必须配置固定图片 Provider：按固定图片、固定 LLM、当前会话 Provider 逐级尝试，并把当前
评论图与原帖图分组。原帖图片只允许把硬限长的描述传给最终 Agent，所有预处理路径失败时直接
移除原帖原图；当前评论自己的图片才允许回退 AstrBot 原生多模态链路。最终可信焦点块仍在这些
背景之后注入，因此这仍是插件侧临时输入整形，不修改 AstrBot system prompt、Conversation、
工具注册或其他平台事件。图片预处理调用额外受事件图片宽限和单事件 60 秒硬预算约束；4.26.2+
同时把单次 Provider 请求重试数限制为 1，4.24.2 即使忽略该兼容参数也仍受外层硬预算保护。超时
视为该预处理路径失败并继续 Agent；无图片事件不会执行该 Provider 路径。

`MessageSession` 与 `TextPart` 在目标版本尚未从更浅的 `astrbot.api` 门面导出，因此分别从
`astrbot.core.platform.astr_message_event` 和 `astrbot.core.agent.message` 导入。这是 4.26.2
实际运行接口，不是复制核心代码。若 AstrBot 后续在 4.x 中移动这两个类型，CI 的包级
API 契约任务会先暴露问题。

## 文档与目标版本不一致时的处理

Plugin Pages 使用当前官方文档描述的新桥接 API。平台适配器行为以 4.26.2/4.26.8 的实际
类型和调用顺序为准。

当前官方“开发一个平台适配器”教程的示例仍写作
`super().__init__(event_queue)`；4.26.2 和 4.26.8 的 `Platform.__init__` 实际签名均为
`(config, event_queue)`，平台管理器以
`cls_type(platform_config, platform_settings, event_queue)` 创建实例。项目因此采用
`super().__init__(platform_config, event_queue)`。这是本次复核确认的文档/源码差异之一。

平台注册器注释将 `logo_path` 描述为相对插件目录；4.26.8 Dashboard
`register_platform_logo()` 实际以适配器类模块文件所在目录进行拼接。适配器类位于
`xiaoheihe/adapter.py`，因此项目使用静态路径 `../logo.png`，解析结果为仓库根目录
`logo.png`。

包级核验确认 AstrBot 4.24.2 尚未包含 `astrbot.api.web`。因此该版本只加载核心平台适配器，
管理页后端仅使用公开 Plugin Page API；该 API 缺失时核心适配器仍可加载。
完整扫码登录和管理页要求 AstrBot 4.26.2。已有凭证的数据目录可让核心适配器在 4.24.2
加载，但首次登录应在 4.26.2 完成。这个差异来自官方包本身，项目以公开接口作为兼容边界。

最低版本 4.24.2 的本机端到端验证状态为“待验证”；仓库通过 CI 的 `astrbot-compat`
矩阵持续检查 4.24.2 和 4.26.2，并由
`astrbot-latest-stable` 任务动态安装 `<5` 的最新稳定包。当前 4.27.2 wheel 的 13 项
文件/符号契约也已在本机离线检查通过。发布前仍需在 4.24.2、4.26.2 和当前稳定版各完成
一次插件加载、适配器创建和模拟运行人工验收。

## 覆盖更新的数据边界

AstrBot 4.26.8 的插件更新器替换 `data/plugins/<插件目录>`。本项目通过
`StarTools.get_data_dir(PLUGIN_NAME)` 使用
`data/plugin_data/astrbot_plugin_xiaoheihe_adapter/`，因此常规更新继续使用：

- `credentials/<profile_id>.json` 登录凭证和稳定设备身份；
- `xiaoheihe.db` 事件、去重键、通知游标、发送记录和审核候选；
- 插件日志与缓存；
- AstrBot 保存的同一个 `AstrBotConfig`。

数据库打开时按 `schema_migrations` 顺序执行增量迁移。v1.2.13 的最新迁移版本仍为 v6；除旧版
按浏览量统计的 `proactive_count` 会清零并切换为主动 AI 请求口径外，其余已有记录原位保留。
卸载时显式删除插件数据、手动删除数据目录或更改插件内部名称属于新的数据边界；更新前备份
插件数据目录可用于回滚。

## 架构边界

- 模型与 Agent 请求统一由 AstrBot 原生事件管线执行；
- 配置保存统一使用插件 `AstrBotConfig.save_config()`；
- 插件功能入口为平台适配器与 Plugin Page；
- 帖子背景以临时用户内容注入，system prompt 由 AstrBot 人格管理；
- AstrBot 源码保持为外部运行时依赖。

## 参考

- [AstrBot 插件开发](https://docs.astrbot.app/dev/star/plugin-new.html)
- [AstrBot 平台适配器开发](https://docs.astrbot.app/dev/plugin-platform-adapter.html)
- [AstrBot Plugin Pages](https://docs.astrbot.app/en/dev/star/guides/plugin-pages.html)
- [AstrBot 4.26.8 Platform 源码](https://github.com/AstrBotDevs/AstrBot/blob/v4.26.8/astrbot/core/platform/platform.py)
- [AstrBot 4.26.8 平台管理器](https://github.com/AstrBotDevs/AstrBot/blob/v4.26.8/astrbot/core/platform/manager.py)
