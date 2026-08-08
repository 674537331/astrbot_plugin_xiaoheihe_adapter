# 架构

## 数据流

```text
小黑盒 HTTP API
  → XiaoheiheApiClient（连接池、签名、限流、重试、脱敏）
  → NotificationService / FeedService
  → notification_cursors（分账号、分通知类型 message_id 边界）
  → Repository（先持久化再入队、事务幂等）+ PermissionService + ContextBuilder
  → XiaoheihePlatformAdapter
  → AstrBotMessage + XiaoheiheMessageEvent
  → Platform.commit_event()
  → 楼层共享 session + 每轮发送者 UID 持久化到 LLM 用户历史
  → 被动楼层焦点路由（当前消息 > 直接回复对象 > 最近楼层 > 原帖背景）
  → 长被动楼层按来源语义压缩（原帖 / 最近楼层分离；当前消息与直接回复对象保留原文）
  → 被动楼层图片预处理（当前评论图片 > 原帖图片；原帖原图 fail-closed）
  → AstrBot 原生会话 / 人格 / 记忆 / Agent / MCP / Skills / Tools
  → 指定工具兼容层（Grok 普通网页查询临时隔离事件原图并在调用后恢复）
  → Agent 生命周期跟踪 + XiaoheiheMessageEvent 回复聚合
  → 模拟运行 / feed candidate / 无审核直发 / 单条真实评论
```

插件只负责平台输入输出。模型、人格、会话历史、长期记忆、Agent Runner 和工具执行全部由
AstrBot 原生管线负责。

## 模块职责

- `adapter.py`：平台注册、生命周期、原生消息转换、事件提交、主动会话发送。
- `event.py`：结构化路由、Agent 中间/控制消息分类、一次发送保护、插件直接结果去重，以及任意数量的流式与 AstrBot 分段文本聚合。
- `api_client.py`：单账号长生命周期异步客户端和结构化错误。
- `endpoints.py` / `parsers.py` / `request_signing.py`：隔离不稳定的外部契约。
- `auth.py`：二维码状态机与原子凭证存储。
- `notification_service.py`：`message_id` 分页边界、有界优先队列、首次基线和到期重试恢复。
- `context_builder.py`：帖子/楼层缓存、内容清洗、图片 URL 校验、临时上下文，以及被动回复与主动刷帖分离的上下文预算/焦点路由和图片来源标记。
- `context_compression.py`：长楼层压缩输入/JSON 输出契约、原帖与楼层摘要硬上限、话题迁移标记，以及来源感知图片描述提示词；所有生成结果仍按不可信社区背景处理。
- `permission_service.py`：自身、黑名单、主人、白名单、普通触发的固定优先级。
- `feed_service.py`：高风险主动刷帖筛选、候选、人工审核与无审核直发入口。
- `database.py` / `repository.py`：迁移、事务、索引、幂等、保留和诊断。
- `config_service.py`：同一个 `AstrBotConfig` 的校验、保存和热重载通知。

主插件的工具钩子只在平台为 `xiaoheihe` 且工具名为 `grok_web_search` 时介入。普通网页查询
暂时从事件消息链移出顶层 `Image`，避免第三方 Grok 插件再次自动提取原图；工具返回后恢复原
位置，Agent 完成阶段还有异常兜底。查询明确要求搜图/识图时不隔离原图，其他工具不修改消息链。

插件覆盖更新时，AstrBot 会先结束旧插件运行时。新插件实例在 `Star.initialize()` 阶段
检查平台管理器：冷启动保持 AstrBot 原生创建顺序；热重载阶段则重建已启用的
`xiaoheihe` 实例，使通知轮询、回复 worker 和主动帖子任务绑定到新的运行时。管理页状态
同时保留已配置实例清单，恢复失败时显示停止状态和脱敏错误提醒。
- `task_manager.py` / `runtime.py`：任务、客户端、数据库、锁、SSE 和析构。
- `web_api.py`：认证后的 Plugin Page 后端。

## 会话与路由

路由对象始终保存：

```text
profile_id / post_id / root_comment_id / parent_comment_id / notification_id
```

会话是可逆且确定性的：

- 帖子：`xhh_post_<post_id>`
- 楼层：`xhh_thread_<post_id>_<root_comment_id>`
- group ID：`xhh_post_<post_id>`
- message ID：`xhh_<event_type>_<notification_id>_<external_comment_id>`

`send_by_session()` 只接受上述格式；完整恢复帖子和楼层后才进入发送，目标信息缺失时直接
返回明确错误。

同一楼层中的不同用户刻意共享同一个 `xhh_thread_*`，以保留公开讨论的连续语境；发送者身份
不进入 session ID。每个 `AstrBotMessage.sender.user_id` 始终使用当前通知的真实小黑盒 UID，
并在 `on_llm_request` 中额外追加非临时 `<xiaoheihe_sender_identity uid="...">` 内容块。
AstrBot 会将普通 `extra_user_content_parts` 持久化到该轮 `role=user` 历史，因此后续即使 A、B、C
共用一个 Conversation，每一轮历史仍带有各自 UID。帖子、楼层全文和实时状态继续使用
`mark_as_temp()`，只参与当前请求，不重复写入历史。可信运行时元数据也会指出当前触发 UID，
要求模型按 UID 区分不同发言人的第一人称。

v1.2.12 在不改变上述 session 和持久化身份模型的前提下，对临时社区背景增加事件类型相关的
焦点路由。评论回复/@ 以当前原生用户消息为最高优先级，从通知引用关系或楼层父评论中单独
提取直接回复对象，再提供最近楼层窗口，最后才提供低相关性的原帖背景；当前入站评论和直接
回复对象会从普通楼层窗口去重。默认楼层回复原帖预算为 1600 字、最近窗口为 12 条且单条最多
800 字，并在临时背景尾部重复当前消息作为定位副本，再追加不含社区正文的可信焦点规则。
主动推荐流不套用这些缩减预算，继续以原帖为主要话题并使用完整的全局帖子/评论上限。

v1.2.13 将上述 1600 字 / 12 条策略改为短上下文和故障路径的确定性兜底。被动楼层的可压缩
背景超过默认 2400 字时，适配器在 `on_llm_request` 中先调用可选的上下文 Provider；压缩器最多
接收 8000 字原帖正文和合计 8000 字最近楼层，并要求把 `post_summary`、`thread_summary`、
`local_topic`、`relation_to_post` 分字段返回。输出再次由本地代码硬限制为配置的 700 / 1400
字等预算，当前消息和直接回复对象不使用压缩结果替代。压缩失败、超时或返回格式异常不会失败
本轮事件，而是直接使用 v1.2.12 背景。可信焦点块改为在文本压缩和图片背景之后最后注入。

v1.2.14 在压缩源中额外保存本轮实际纳入窗口的参与者身份，并修复 `on_llm_request` 二次转换时
遗漏该字段的问题。每条楼层在交给压缩器前已经按 `昵称 (UID ...)` 标注；本地代码从最终保留的
楼层行生成去重身份锚点，压缩 Provider 只负责语义归纳并被要求原样使用这些身份。压缩完成后
身份锚点再次由本地代码附回不可信临时上下文，所以昵称/UID 映射不依赖模型摘要是否完整。当前
消息和直接回复对象仍走原有未压缩路径；system prompt 与 AstrBot 人格不由适配器修改。

辅助图片转述/上下文压缩调用新增进程内 Provider 冷却：401/403、429、超时和普通异常分别进入
有界冷却窗口，后续事件在冷却期间跳过该额外调用并继续备用 Provider 或确定性降级。冷却不会
改变主 Agent Provider；Plugin Page 的状态接口暴露用途、Provider、剩余秒数和最近错误，并允许
Dashboard 用户手动清除单个冷却。原帖图片的清洗后文字转述另有最多 256 条、30 分钟的内存 LRU
缓存，不保存图片二进制。

图片 URL 在 `ContextBuilder` 收集阶段同时记录 `current_comment` / `original_post` 来源。被动楼层
回复在主 Agent 请求前强制分组预处理图片，Provider 按“固定图片 → 固定 LLM → 当前会话”逐级
尝试；原帖图片生成较短的低优先级描述，输出再由本地代码硬截断，原帖原图不会进入最终回答
模型。所有 Provider 都失败时，原帖图片 fail-closed 并只留下不含社区内容的可信失败说明；当前
评论自己的图片则保留为最高相关性的 AstrBot 原生视觉兜底。主动推荐流不使用这套被动楼层
fail-closed 路由，原帖文字和图片继续作为主要内容。图片 LLM 预处理只消费事件已有的图片额外
回复宽限，所有顺序 Provider 尝试共享同一截止时间并额外硬限 60 秒；预算耗尽立即走上述降级，
不继续占用最终 Agent 或工具链的回复时间。无图片事件不创建该预处理调用链。

## 事件状态

```text
claimed → context_ready → dispatched → generated
                                      ├─ sent
                                      ├─ dry_run
                                      ├─ send_unknown
                                      ├─ retry_wait
                                      └─ dead_letter
claimed → ignored
```

`dispatched` 在提交 AstrBot 队列前写入，避免快速完成的 `sent/dry_run` 被较旧状态覆盖。
最终完成仅包括发送成功、成功模拟运行、明确忽略或人工丢弃。

热重载恢复采用保守策略：`claimed/context_ready` 且没有发送记录的事件可继续处理；
`dispatched` 事件隔离为失败终态；已有 `sending/send_unknown` 记录的事件进入人工核对状态；
已有失败发送记录的事件进入 `dead_letter`。该策略优先保证一个外部通知最多触发一次评论。

## 并发

- 每个 `profile_id` 只有一个轮询器；
- 有界优先队列限制总积压和单用户积压；
- 首次轮询把当前最新 `message_id` 写入 `notification_cursors`，默认从此后的新通知开始；
- 新通知先原子写入 SQLite 再进入队列；扫描能在单轮内碰到旧边界时直接推进实时游标；
- 单轮页数达到上限但尚未碰到旧边界时，未完成连续区间写入 `notification_backfills` 后再推进
  实时 `notification_cursors`；后续轮询优先处理新通知，并用剩余分页预算继续回填旧区间；
- 回填 offset 与最旧边界均持久化，插件重启后继续；回填期间再次出现超大新通知区间时会把 offset
  安全回退到新的连续扫描末端，同时保留最旧边界，重复项继续由 `incoming_events` 唯一约束去重；
- 队列或单用户上限触发时写入 `retry_wait`，每轮从 SQLite 主动恢复到期事件；
- SQLite 唯一约束、发送记录和进程内事件键共同过滤重复通知；
- 主人事件提高优先级但不突破硬上限；
- 帖子/楼层上下文网络读取在锁外完成；同 key 缓存 miss 通过 in-flight Task 合并为一次网络读取，
  默认 TTL 为 60 秒；通知已携带原帖快照的楼层只补取 root 评论树；同一楼层从原生事件提交到
  最终发送或超时仍使用串行锁，不同楼层受 worker 数量限制；
- HTTP Client 使用账号级并发初始化闸门，存活连接池直接复用；事件分发复用客户端内存凭证，
  不再逐事件读取凭证文件；图片 URL 去重且同一事件相同 hostname 只解析一次；
- 不在锁内执行轮询或上下文网络请求；
- 所有任务由 `TaskManager` 持有并在 terminate 时取消等待。

## 安全边界

- 凭证不进入配置、WebUI、日志或诊断；
- Web API 要求 Dashboard 已认证用户，并重新校验所有输入；
- UI 只用 `textContent` 创建外部内容，避免 XSS；
- SQL 全部参数化；动态排序/表名只来自后端固定白名单；
- 图片仅接受无用户信息的公开 HTTPS URL，并在提交组件前校验 DNS 解析结果；
- v1.1.2 使用图片 URL 直传，本地图片字节缓存保持为空；v1.2.14 只增加有界的图片转述文本缓存，
  不预下载图片，因此移除从未实际生效的单图/总图 MB 与图片缓存 MB 配置；
- v1.1.3 按进入 AstrBot 的图片数量为基础回复超时增加视觉处理宽限，单图宽限受限于 15–60 秒，事件总截止时间最高 900 秒；
- 评论区 @ 分别读取原帖详情与指定楼层，合并通知内原帖快照；评论图和原帖图交替进入图片上限；
- 外部内容置于 `<xiaoheihe_context trust="untrusted">` 用户侧临时片段中；
- 当前触发 UID 由通知解析结果写入可信运行时元数据；持久化历史只记录结构化 UID 身份标签，昵称和社区正文仍按外部内容处理；
- POST 评论超时进入 `send_unknown`，核对结果不明确时保持人工检查状态；
- 评论接口明确返回 `status=failed` 时进入失败终态，事件级发送闸门拦截第二次 POST；
- 主动候选批准先原子转换为 `sending`，账号级审核锁限制并发；更新或重启遗留的
  `sending` 转为 `send_unknown`；
- 主动候选真实发送复用与普通回复相同的楼层锁、发送记录、自身评论记录和超时核对链路。
- 无审核主动回复跳过候选表，但继续复用事件级发送记录、楼层锁、重复发送闸门和超时核对链路。
