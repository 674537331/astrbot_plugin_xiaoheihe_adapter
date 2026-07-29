# 架构

## 数据流

```text
小黑盒 HTTP API
  → XiaoheiheApiClient（连接池、签名、限流、重试、脱敏）
  → NotificationService / FeedService
  → Repository（事务幂等）+ PermissionService + ContextBuilder
  → XiaoheihePlatformAdapter
  → AstrBotMessage + XiaoheiheMessageEvent
  → Platform.commit_event()
  → AstrBot 原生会话 / 人格 / 记忆 / Agent / MCP / Skills / Tools
  → XiaoheiheMessageEvent.send()
  → dry-run / feed candidate / 单条真实评论
```

插件只负责平台输入输出。模型、人格、会话历史、长期记忆、Agent Runner 和工具执行全部由
AstrBot 原生管线负责。

## 模块职责

- `adapter.py`：平台注册、生命周期、原生消息转换、事件提交、主动会话发送。
- `event.py`：结构化路由、一次发送保护、流式文本聚合。
- `api_client.py`：单账号长生命周期异步客户端和结构化错误。
- `endpoints.py` / `parsers.py` / `request_signing.py`：隔离不稳定的外部契约。
- `auth.py`：二维码状态机与原子凭证存储。
- `notification_service.py`：分页轮询、有界优先队列、首次基线和状态推进。
- `context_builder.py`：帖子/楼层缓存、内容清洗、图片 URL 校验、临时上下文。
- `permission_service.py`：自身、黑名单、主人、白名单、普通触发的固定优先级。
- `feed_service.py`：高风险主动刷帖筛选、候选与人工审核。
- `database.py` / `repository.py`：迁移、事务、索引、幂等、保留和诊断。
- `config_service.py`：同一个 `AstrBotConfig` 的校验、保存和热重载通知。
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
最终完成仅包括发送成功、成功 dry-run、明确忽略或人工丢弃。

## 并发

- 每个 `profile_id` 只有一个轮询器；
- 有界优先队列限制总积压和单用户积压；
- 主人事件提高优先级但不突破硬上限；
- 帖子/楼层上下文网络读取在锁外完成；同一楼层从原生事件提交到最终发送或超时使用串行锁，
  不同楼层受 worker 数量限制；
- HTTP Client、TTL 上下文缓存和图片 URL 都复用或去重；
- 不在锁内执行轮询或上下文网络请求；
- 所有任务由 `TaskManager` 持有并在 terminate 时取消等待。

## 安全边界

- 凭证不进入配置、WebUI、日志或诊断；
- Web API 要求 Dashboard 已认证用户，并重新校验所有输入；
- UI 只用 `textContent` 创建外部内容，避免 XSS；
- SQL 全部参数化；动态排序/表名只来自后端固定白名单；
- 图片仅接受无用户信息的公开 HTTPS URL，并在提交组件前校验 DNS 解析结果；
- v1.0.0 不下载图片，因此没有本地图片缓存或重定向链；
- 外部内容置于 `<xiaoheihe_context trust="untrusted">` 用户侧临时片段中；
- POST 评论超时进入 `send_unknown`，核对不到时不盲目重试。
- 主动候选批准复用与普通回复相同的楼层锁、发送记录、自身评论记录和超时核对链路。
