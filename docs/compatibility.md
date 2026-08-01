# AstrBot 兼容性说明（v1.2.6）

## 调查范围

项目面向 AstrBot `>=4.24.2,<5`，重点兼容 4.26.2；接收与真实评论链路的最新用户复测环境为
AstrBot 4.26.8。v1.2.6 保持覆盖更新后的平台实例协调、评论与原帖上下文合并，并为多图视觉预处理提供按图片数量计算的回复时间。
2026-08-02 发布 v1.2.6 前再次核对：

- AstrBot 4.26.2 标签对应源码快照（提交 `a619988d2d181c884f7bf04e24f30c0ea0928ff6`）；
- AstrBot 4.26.8 标签中的 `Platform`、平台管理器、注册器、消息和事件源码；
- PyPI 已发布的最低版本 4.24.2、重点版本 4.26.2，以及 2026-07-28 的当前稳定版
  4.26.8 wheel；
- AstrBot 当前官方插件开发、平台适配器和 Plugin Pages 文档；
- `Platform`、`PlatformMetadata`、`register_platform_adapter`、`AstrBotMessage`、
  `AstrMessageEvent`、`MessageSession`、`commit_event()`、`send_by_session()`；
- `AstrBotConfig.save_config()`、`StarTools.get_data_dir()`、插件 `terminate()`；
- `Image` 消息组件、`on_llm_request`、`extra_user_content_parts` 与
  `TextPart.mark_as_temp()`；
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
| 分段回复 | `on_llm_response()` + `AstrMessageEvent.get_result()` + 非流式平台 `send()` | 分段清理前保存完整文本，任意数量的分段只提交一条小黑盒评论 |
| 事件提交 | `Platform.commit_event(event)` | 进入 AstrBot 原生事件队列；模型调用由 AstrBot 核心负责 |
| 固定 LLM Provider | 事件入队前设置 `selected_provider` extra | 兼容 4.24.2 与 4.26.2 的主 Agent Provider 选择时序；留空时继续使用会话或全局 Provider |
| 主动发送 | `send_by_session(MessageSession, MessageChain)` | 从确定性 session ID 恢复路由；失败时抛出明确错误 |
| 唤醒 | `event.is_wake` 和 `event.is_at_or_wake_command` | @ 与直接回复不依赖正文仍保留 `@昵称` |
| 临时上下文 | `filter.on_llm_request()` + `request.extra_user_content_parts` + `TextPart(...).mark_as_temp()` | 背景仅注入本轮用户侧内容，不改 system prompt，不永久污染会话 |
| 图片 | `astrbot.api.message_components.Image(file=url, url=url)` | v1.0.0 只传经过安全校验的公开 HTTPS URL，交给 AstrBot 媒体链路 |
| 固定图片 Provider | `Context.get_provider_by_id()` + `Provider.text_chat()` | 成功时注入临时图片描述；失败或空结果时保留原图并回退主 Provider 与 AstrBot 原生图片流程 |
| 视觉降级 | `Context.get_provider_by_id()` / `get_using_provider()` + provider `modalities` | 按固定或当前主 Provider 检查能力；仅在明确不含 `image` 时移除图片并保留文本 |
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

AstrBot 4.26.2/4.26.8 的 `RespondStage` 在全局“分段回复”开启时，会按组件多次调用平台
事件的 `send()`，即使平台元数据声明 `support_streaming_message=False`。v1.1.2 在首次
`on_llm_response(priority=-1000)` 保存最终模型文本，再在首次 `send()` 时优先恢复该文本；
若插件链未提供最终文本，则回退到同一事件的完整 `MessageEventResult.chain`。三段、五段
或更多分段均只产生一条评论，分段词清理前的标点和换行也得到保留。管理页会显示兼容提醒，
关闭 AstrBot 分段回复可减少等待，插件配置和数据目录保持原样。

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
`astrbot-latest-stable` 任务动态安装 `<5` 的最新稳定包。当前 4.26.8 wheel 的 13 项
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

数据库打开时按 `schema_migrations` 顺序执行增量迁移。v1.2.6 的最新迁移版本为 v6；除旧版
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
