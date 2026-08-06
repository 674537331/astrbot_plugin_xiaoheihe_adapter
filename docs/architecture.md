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
- `context_builder.py`：帖子/楼层缓存、内容清洗、图片 URL 校验、临时上下文。
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
- 新通知先原子写入 SQLite 再进入队列，持久化成功且扫描到旧边界后才推进游标；
- 队列或单用户上限触发时写入 `retry_wait`，每轮从 SQLite 主动恢复到期事件；
- SQLite 唯一约束、发送记录和进程内事件键共同过滤重复通知；
- 主人事件提高优先级但不突破硬上限；
- 帖子/楼层上下文网络读取在锁外完成；同一楼层从原生事件提交到最终发送或超时使用串行锁，
  不同楼层受 worker 数量限制；
- HTTP Client 使用账号级并发初始化闸门，存活连接池直接复用；TTL 上下文缓存和图片 URL 均复用或去重；
- 不在锁内执行轮询或上下文网络请求；
- 所有任务由 `TaskManager` 持有并在 terminate 时取消等待。

## 安全边界

- 凭证不进入配置、WebUI、日志或诊断；
- Web API 要求 Dashboard 已认证用户，并重新校验所有输入；
- UI 只用 `textContent` 创建外部内容，避免 XSS；
- SQL 全部参数化；动态排序/表名只来自后端固定白名单；
- 图片仅接受无用户信息的公开 HTTPS URL，并在提交组件前校验 DNS 解析结果；
- v1.1.2 使用图片 URL 直传，本地图片缓存保持为空；
- v1.1.3 按进入 AstrBot 的图片数量为基础回复超时增加视觉处理宽限，单图宽限受限于 15–60 秒，事件总截止时间最高 900 秒；
- 评论区 @ 分别读取原帖详情与指定楼层，合并通知内原帖快照；评论图和原帖图交替进入图片上限；
- 外部内容置于 `<xiaoheihe_context trust="untrusted">` 用户侧临时片段中；
- POST 评论超时进入 `send_unknown`，核对结果不明确时保持人工检查状态；
- 评论接口明确返回 `status=failed` 时进入失败终态，事件级发送闸门拦截第二次 POST；
- 主动候选批准先原子转换为 `sending`，账号级审核锁限制并发；更新或重启遗留的
  `sending` 转为 `send_unknown`；
- 主动候选真实发送复用与普通回复相同的楼层锁、发送记录、自身评论记录和超时核对链路。
- 无审核主动回复跳过候选表，但继续复用事件级发送记录、楼层锁、重复发送闸门和超时核对链路。
