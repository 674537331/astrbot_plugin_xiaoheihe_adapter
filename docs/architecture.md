# 架构说明（v1.3.2）

## 总体边界

插件是 AstrBot 的小黑盒平台接入层。小黑盒 HTTP 数据经过本插件规范化、权限过滤和上下文构建后，以 `AstrBotMessage` / `XiaoheiheMessageEvent` 提交给 AstrBot；人格、Conversation、Agent Runner、MCP、Skills、工具调用和最终模型执行仍由 AstrBot 原生管线负责。

```text
小黑盒 HTTP API
  → XiaoheiheApiClient（连接池 / 签名 / 限流 / 重试 / 脱敏）
  → NotificationService / FeedService / TopicService
  → Repository（SQLite：游标 / 幂等 / 候选 / 发送记录 / 快照）
  → PermissionService + ContextBuilder
  → XiaoheihePlatformAdapter
  → AstrBotMessage + XiaoheiheMessageEvent
  → Platform.commit_event()
  → AstrBot 原生 Agent / 人格 / 会话 / 工具
  → XiaoheiheMessageEvent 聚合最终回复
  → dry-run / 审核候选 / 真实评论
```

## 主要模块

- `adapter.py`：平台注册、生命周期、原生消息转换、事件提交、`send_by_session()`。
- `event.py`：路由恢复、Agent 中间/控制消息过滤、流式/分段文本聚合、一次发送保护。
- `api_client.py`：长生命周期异步 HTTP Client、签名、重试、认证失效与结构化错误。
- `endpoints.py`：集中维护小黑盒端点，隔离非公开接口变化。
- `parsers.py`：通知、帖子、评论和 feed 响应规范化。
- `auth.py`：二维码登录状态机、凭证原子存储。
- `notification_service.py`：`message_id` 边界、分页、回填、有界队列和到期重试恢复。
- `topic_service.py`：真实分区目录与真实分区帖子流的只读 GET 封装、`topic_id` 校验和目录扁平化。
- `feed_service.py`：主动浏览来源选择、帖子过滤、AI 请求限额、候选审核和无审核直发入口。
- `context_builder.py` / `context_compression.py`：帖子/楼层上下文、焦点路由、身份锚点、长楼层压缩。
- `provider_routing.py`：主模型和图片 Provider 路由计算。
- `database.py` / `repository.py`：SQLite 迁移、索引、幂等、发送闸门、视觉快照、保留与诊断。
- `config_service.py`：配置迁移、校验、保存、Plugin Page 通用 schema 过滤和后台任务刷新通知。
- `runtime.py` / `task_manager.py`：客户端、数据库、后台任务、锁、SSE、熔断和析构。
- `web_api.py`：受 AstrBot Dashboard 会话保护的 Plugin Page API。

## 主动浏览来源路由

v1.3.0 起的来源选择不是“多个过滤器同时工作”，而是明确的优先级路由：

```text
proactive_feed.topic_ids 是否为空？
  ├─ 是
  │   → GET /bbs/app/feeds
  │   → fallback_sources 本地标签分类
  │   → 安全过滤 / 去重 / AI
  │
  └─ 否
      → 最多 4 路并发 GET /bbs/app/topic/feeds
      → 按帖子 ID 跨分区去重
      → 按发布时间 / 热度合并排序
      → 至少一个真实分区请求成功？
          ├─ 是 → 只处理真实分区结果
          └─ 否 → 仅额外 GET 一次 /bbs/app/feeds
                  → fallback_sources 本地分类
                  → 安全过滤 / 去重 / AI
```

### 关键不变量

1. **真实分区优先。** `topic_ids` 非空时，不先拉推荐流。
2. **部分失败不触发回退。** 例如 3 个真实分区中 1 个成功、2 个失败，本轮只使用成功分区的帖子。
3. **全部失败才回退。** 只有所有真实分区请求都失败，才额外拉一次推荐流。
4. **取消不是失败。** `asyncio.CancelledError` 会继续向上传播，热重载/停机不会误触发推荐流请求。
5. **推荐流分类只是兼容过滤。** `fallback_sources` 仅匹配 `/bbs/app/feeds` 返回帖子的 `section_names`/主题标签，不是服务端真实分区参数。
6. **AI 限额在帖子合格后才占用。** 网络浏览、本地分类和被过滤帖子不消耗主动 AI 请求额度。

## 真实分区探测

Plugin Page “浏览来源 → 探测/刷新真实分区”调用：

```text
GET Plugin Page API /feed/topics/probe
  → GET /bbs/app/api/topic/index/?type=list
  → 解析真实 topic_id / 名称 / 分组
  → 最多尝试 5 个候选 topic_id 验证分区帖子流
  → 每次最多读取 3 条样例
  → 只返回脱敏、有限的目录和验证信息
```

探测不会调用 AI，不会执行评论 POST，也不会修改小黑盒账号状态。

## 配置持久化

主动浏览来源有三个与升级相关的字段：

```text
source             # v1.2.x 旧单选字段，仅用于迁移
topic_ids          # v1.3.0 真实分区 ID 列表
fallback_sources   # v1.3.0 推荐流多选回退分类
```

这三个字段都保留在 `_conf_schema.json` 中并设置 `invisible: true`，原因是 AstrBot 会按 schema 对配置补默认值/归一化；字段不能仅存在于 `DEFAULT_CONFIG`，否则独立页面保存的值可能在配置加载时被移除。

Plugin Page 的通用“设置”表单通过 `ConfigService.ui_schema()` 再移除这三个字段，因此来源只在独立“浏览来源”页面编辑。

升级旧配置时，如果 AstrBot 已提前补入 schema 默认 `fallback_sources=["all"]`，但仍存在旧非默认 `source`，迁移器会把旧值转换为新列表；若已经存在新的非默认 `fallback_sources`，则新配置优先。

## 会话与身份

路由对象保存：

```text
profile_id / post_id / root_comment_id / parent_comment_id / notification_id
```

确定性会话：

```text
帖子 session  xhh_post_<post_id>
楼层 session  xhh_thread_<post_id>_<root_comment_id>
group ID      xhh_post_<post_id>
message ID    xhh_<event_type>_<notification_id>_<external_comment_id>
```

同一楼层中的不同用户共享公开讨论 session，但每一轮都保留发送者昵称和真实 UID；API 缺少 UID 时使用按事件隔离的本地身份锚点。备用锚点不能获得主人、管理员或 UID 白名单权限。

v1.3.2 将“身份正确归属”和“身份在提示中的显著性”拆开处理：当前发送者完整身份只由非临时、可信的 `xiaoheihe_sender_identity` 绑定负责跨轮历史归属；`runtime_context` 与临时社区背景只引用这一绑定，不再重复展开当前昵称、UID 或本地锚点。原帖作者和楼层参与者也按来源尽量只展开一次完整身份。

长楼层压缩继续把本地提取的身份映射成 `speaker_n` 后交给压缩 Provider，Provider 只能返回既有 `speaker_key`。本地渲染时，有摘要的参与者在摘要行展开一次完整身份；已经在“当前消息直接回复对象”原文中绑定的参与者只保留 `speaker_n` 引用，不再次展开昵称/UID；其余必要参与者才保留一次身份→`speaker_n` 映射。任何未知或编造的 speaker key 仍拒绝整份压缩结果并回退确定性上下文。

模型上下文明确规定昵称、UID 和本地身份锚点只用于内容归属与第一人称消歧。除非身份本身就是当前问题，或多人讨论确实需要点名消歧，否则最终回复正文不应主动称呼、复述或评价这些身份值。

被动楼层焦点顺序：

```text
当前原生用户消息
  > 直接回复对象
  > 最近楼层对话
  > 原帖背景
```

长楼层可额外调用上下文 Provider 做来源感知压缩；当前消息和直接回复对象始终保留原文。压缩失败时回到有界确定性窗口。

## 图片链路

小黑盒图片会在 AstrBot 构建主 Agent 之前按来源处理：

```text
插件 image_provider_id
  → AstrBot 默认图片转述 Provider
  → AstrBot 当前主模型
```

插件只保存安全公开 HTTPS URL 的有限引用；成功识图后向最终 Agent 提供来源明确的文字描述。图片所有者的昵称、UID、身份角色与本地锚点仍作为程序级归属数据参与所有权判断，但 v1.3.2 明确禁止图片压缩 Provider 把这些身份值写入视觉摘要。只有所有者 UID 与当前发送者 UID 完全一致时才允许称为“你发的图片”；UID 或来源无法确认时继续 fail-closed。

全部候选失败、超时或返回占位结果时移除原图并注入可信失败说明，禁止最终模型猜图。主动帖子成功识图可生成 24 小时视觉文字快照。内存 LRU 和 SQLite 都只保存文字描述、图片指纹和脱敏元数据，不保存图片字节。

## 发送与幂等

真实评论发送前会先记录 outgoing attempt。已发送、正在发送或状态未知的事件都有数据库闸门：

- 已确认发送：直接复用记录，禁止第二次 POST；
- `sending` / `send_unknown`：先查询近期机器人评论尝试确认；不能确认时停止自动重发；
- 明确失败：进入终态或按限定错误类型调度安全重试；
- 任务在 POST 后被取消：标为 `send_unknown`，不假定服务端未收到。

主动审核候选额外使用账号级并发锁和原子 claim，避免两个批准请求同时发送。

## 生命周期与性能

- 每账号复用长生命周期 HTTP Client；
- 网络上下文有界 TTL + single-flight；
- 主动真实分区最多 4 路并发；
- FeedService 初始化时预计算真实分区 ID、推荐流别名、关键词、帖子类型和作者黑名单；
- 图片文字快照内存 LRU 有硬上限；
- 任务关闭时传播取消信号并关闭 HTTP Client、SQLite 和 SSE；
- v1.3.2 **没有新增数据库迁移、配置项或运行依赖**。

历史架构演进请查看 [CHANGELOG.md](../CHANGELOG.md)。
