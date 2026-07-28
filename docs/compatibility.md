# AstrBot 兼容性说明

## 调查范围

v1.0.0 面向 AstrBot `>=4.24.2,<5`，重点版本为用户当前使用的 4.26.2。开发前核对了：

- AstrBot 4.26.2 标签对应源码快照（提交 `a619988d2d181c884f7bf04e24f30c0ea0928ff6`）；
- PyPI 已发布的最低版本 4.24.2、重点版本 4.26.2，以及 2026-07-28 的当前稳定版
  4.26.7 wheel；
- AstrBot 当前官方插件开发、平台适配器和 Plugin Pages 文档；
- `Platform`、`PlatformMetadata`、`register_platform_adapter`、`AstrBotMessage`、
  `AstrMessageEvent`、`MessageSession`、`commit_event()`、`send_by_session()`；
- `AstrBotConfig.save_config()`、`StarTools.get_data_dir()`、插件 `terminate()`；
- `Image` 消息组件、`on_llm_request`、`extra_user_content_parts` 与
  `TextPart.mark_as_temp()`；
- Plugin Page 的 `window.AstrBotPluginPage`、`context.register_web_api()` 和
  `astrbot.api.web`。

本仓库不复制或内置 AstrBot 源码。运行时由 AstrBot 提供这些 API；单元测试使用仅覆盖
接口形状的本地桩。CI 另从 PyPI 获取最低版本与重点版本包，核验实际 API 文件和所需符号。

## v1.0.0 采用的接口

| 能力 | 采用方式 | 兼容性结论 |
| --- | --- | --- |
| 适配器注册 | `register_platform_adapter(..., default_config_tmpl=..., adapter_display_name=..., support_streaming_message=False)` | 让“机器人 → 新增适配器”显示“小黑盒”；平台配置只含 `id/enable/profile_id` |
| 适配器构造 | `Platform(platform_config, event_queue)`；适配器构造函数接收 `platform_config/platform_settings/event_queue` | 与 4.26.2 的平台管理器调用方式一致 |
| 元数据 | `PlatformMetadata` | 声明内部名、实例 ID、展示名、默认配置和非流式能力 |
| 入站消息 | `AstrBotMessage` + `MessageMember` + `Plain/Image` | 设置稳定消息 ID、会话、群组、发送者、自身 UID 和结构化 `raw_message` |
| 事件 | `XiaoheiheMessageEvent(AstrMessageEvent)` | `send()` 完成平台处理后调用父类 `send()` |
| 事件提交 | `Platform.commit_event(event)` | 进入 AstrBot 原生事件队列；适配器不调用模型 |
| 主动发送 | `send_by_session(MessageSession, MessageChain)` | 从确定性 session ID 恢复路由；失败时抛出明确错误 |
| 唤醒 | `event.is_wake` 和 `event.is_at_or_wake_command` | @ 与直接回复不依赖正文仍保留 `@昵称` |
| 临时上下文 | `filter.on_llm_request()` + `request.extra_user_content_parts` + `TextPart(...).mark_as_temp()` | 背景仅注入本轮用户侧内容，不改 system prompt，不永久污染会话 |
| 图片 | `astrbot.api.message_components.Image(file=url, url=url)` | v1.0.0 只传经过安全校验的公开 HTTPS URL，交给 AstrBot 媒体链路 |
| 视觉降级 | `Context.get_using_provider()` + provider `modalities` | 仅在提供商明确不含 `image` 时移除图片并保留文本；未声明能力时遵循 AstrBot 向后兼容语义 |
| 配置 | 构造参数中的 `AstrBotConfig` + `save_config()` | 原生设置和 Plugin Page 共用同一个对象，不写核心配置文件 |
| 数据目录 | `StarTools.get_data_dir(plugin_name)` | 凭证与 SQLite 位于插件专属数据目录，升级不覆盖 |
| Plugin Page | `window.AstrBotPluginPage` + `context.register_web_api()` + `astrbot.api.web` | 4.26.2 完整可用；不读取 Dashboard Cookie/LocalStorage，不操作父页面 DOM |
| 生命周期 | `Star.terminate()`、`Platform.terminate()` | 取消任务、关闭 HTTP Client/SQLite/SSE，重复调用安全 |

`MessageSession` 与 `TextPart` 在目标版本尚未从更浅的 `astrbot.api` 门面导出，因此分别从
`astrbot.core.platform.astr_message_event` 和 `astrbot.core.agent.message` 导入。这是 4.26.2
实际运行接口，不是复制核心代码。若 AstrBot 后续在 4.x 中移动这两个类型，CI 的包级
API 契约任务会先暴露问题。

## 文档与目标版本不一致时的处理

Plugin Pages 使用当前官方文档描述的新桥接 API；不使用旧教程中的 Dashboard 内部对象、
Quart/FastAPI 直接依赖或 Cookie 转发。平台适配器行为以 4.26.2 的实际类型和调用顺序为准。

包级核验确认 AstrBot 4.24.2 尚未包含 `astrbot.api.web`。因此该版本只加载核心平台适配器，
管理页后端安全降级为不可用；不会回退到 Quart/FastAPI 或读取 Dashboard 内部认证信息。
完整扫码登录和管理页要求 AstrBot 4.26.2。已有凭证的数据目录可让核心适配器在 4.24.2
加载，但首次登录应在 4.26.2 完成。这个差异来自官方包本身，不能用旧私有接口无风险填平。

最低版本 4.24.2 未在本机安装启动完整 AstrBot 实例，因此不能声称已完成本机端到端验证；
仓库通过 CI 的 `astrbot-compat` 矩阵持续检查 4.24.2 和 4.26.2，并由
`astrbot-latest-stable` 任务动态安装 `<5` 的最新稳定包。当前 4.26.7 wheel 的 11 项
文件/符号契约也已在本机离线检查通过。发布前仍需在 4.24.2、4.26.2 和当前稳定版各完成
一次插件加载、适配器创建和 dry-run 人工验收。

## 明确没有采用的做法

- 没有独立 OpenAI Base URL、API Key、模型名或聊天请求；
- 没有直接修改 `data/cmd_config.json` 或 AstrBot 核心配置；
- 没有注册聊天命令；
- 没有将帖子内容放入 system prompt；
- 没有复制 AstrBot 源码到插件仓库。

## 参考

- [AstrBot 插件开发](https://docs.astrbot.app/dev/star/plugin-new.html)
- [AstrBot Plugin Pages](https://docs.astrbot.app/en/dev/star/guides/plugin-pages.html)
