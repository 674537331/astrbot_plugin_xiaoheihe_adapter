# 小黑盒 API 契约与验证状态

## 重要声明

小黑盒相关接口属于非公开、可能变化的客户端契约。v1.0.0 的接口行为参考
`SomeOvO/xhhRobot` 的功能思路，并由本项目使用 Python 独立实现。

调查时该参考仓库根目录的许可状态未明确，因此本项目将使用范围限定为功能行为研究。下面
只记录观察到的功能端点名称和本项目自己的解析契约。

真实账号已确认扫码登录、@ 通知路径、`result.messages` 容器和评论 @ 类型 `17`。自动测试
使用脱敏 fixture 与 `httpx.MockTransport`；真实评论发送、帖子树细节和主动帖子流仍按
“待验证”管理。在真实发送验证完成前必须保持模拟运行开启（`dry_run: true`）。

### v1.0.1 扫码兼容修正

用户真实扫码暴露出 v1.0.0 的状态解析不匹配；修正依据仍来自公开参考项目，不包含真实
Cookie 或响应转储。当前契约兼容：

- 二维码字段 `result.qr_url` 和有效期字段 `result.expire`；
- 将 `qr_url` 自带查询参数原样用于 `/account/qr_state/`，不附加臆造参数；
- `result.error == "ok"` 表示确认成功，其他非终止标记继续等待；
- 昵称来自 `result.nickname`，UID 可来自登录响应的 `user_heybox_id` Cookie；
- 获取二维码和查询状态共用同一个长生命周期 Cookie 会话。

该修正已由脱敏 fixture 覆盖；真实账号完整登录成功仍需用户升级后复测确认。

### v1.0.2 凭证字段兼容

第二轮真实扫码复测确认状态已进入成功，但 v1.0.1 未能提取 UID 或凭证。v1.0.2 新增以下
独立解析规则：

- UID：`heyboxid`、`heybox_id`、`user_heybox_id`、`profile.heybox_id` 或
  `account_detail.userid`；
- 登录密钥：`pkey`、`user_pkey` 或已设置的会话 Cookie；
- 状态：`wait` 为等待扫码、`ready` 为已扫码待确认、`ok` 为成功；
- 成功响应只使用 `/account/qr_state/` 的结果字段和同一 HTTP 会话中的 Cookie。

诊断只记录响应字段名和凭证数量，不记录字段值、Cookie、二维码参数或 Token。上述形状已加入
脱敏 fixture，仍需用户对 v1.0.2 进行真实账号复测。

### v1.0.4 通知与评论契约修正

用户真实运行日志确认 v1.0.3 的通知请求取得了非预期的 `result` 数组，原先
`type=at/reply&page=` 的请求假设不成立。再次核对参考项目后，本项目独立实现以下契约：

- @ 通知：`message_type=16&offset=<n>&limit=<n>&no_more=false`；
- 评论/回复：`list_type=0&offset=<n>&limit=<n>&no_more=false`，只保留
  `message_type=1/2`；
- 通知主体：`result.messages`，并兼容顶层结果数组；
- 通知字段：`message_id`、`comment_a_id`、`comment_a_text`、`root_comment_id`、
  `linkid`、`userid_a` 和 `user_a`；
- 帖子正文：`result.link.text` 可以是 JSON 富文本段，图片 URL 与文本分别规范化；
- 评论创建：Workshop 主机上的表单请求，字段为
  `is_cy/link_id/reply_id/root_id/text`。

该修正由脱敏 fixture 和 MockTransport 覆盖。通知参数和字段仍需用户升级后复测；评论写接口
的一次性签名尚未通过真实账号验证，因此必须继续保持模拟运行；真实评论发送状态以真实账号
验证结果为准。

### v1.0.5 真实通知可观测性修正

用户在小黑盒客户端确认“@我的”存在通知，但 v1.0.4 事件记录仍为 0。v1.0.5 为认证请求
补充网页端通用环境参数，并进行以下安全诊断：

- HTTP 200 但 `status` / `stat` 为非成功值时，按上游拒绝处理，不再当作空通知成功；
- 分别记录 mention/reply 的原始条数、解析接收条数、消息类型、列表字段及结果容器类型；
- 诊断不包含消息正文、UID、昵称、帖子 ID、Cookie、Token、设备 ID或签名值；
- 私有 `hkey` 常量与混淆实现保持来源隔离。若接口明确要求其他签名，将继续依据许可清晰的
  来源或小黑盒公开契约独立实现，并按真实验证状态记录结果。

### v1.0.6 hkey 与 relogin

用户真实日志确认通知端点返回 HTTP 200，但业务状态为
`status=relogin, msg=请重新登录`。这表明 v1.0.4 的空列表是认证失败，不是通知为空。
v1.0.6：

- 使用 MIT 许可的 `XiaHouSheng/heybox-core` 作为许可清晰的行为来源，将动态 `hkey` 算法
  独立移植为 Python，并在 `THIRD_PARTY_NOTICES.md` 保留版权与许可；
- 每次请求重新生成 `_time`、大写随机 nonce 和对应路径的 `hkey`；
- 账号检查改用已观察到的 `/bbs/app/api/user/permission`；
- `relogin` 或明确要求重新登录的响应转换为 `CredentialInvalidError`，触发 AstrBot
  运行时熔断与适配器任务刷新；
- 运行时保持纯 Python，并将许可证未明确项目的内容限定为行为研究。

上述算法有固定向量和 MockTransport 测试，但通知接口仍需用户重新扫码后真实复测。
Workshop 评论所需 `_rnd` 不在本次范围，继续标记为待真实验证。

### v1.0.7 Web 登录身份修正

用户在 v1.0.6 重新登录后，账号检查仍返回 `status=failed`。重新核对
`SomeOvO/xhhRobot` 的公开行为以及 MIT 许可的 `HadeonYu/heybox-bot` 后确认：

- 获取二维码和检查二维码状态都携带完整的 Web 客户端查询参数；
- 两个请求必须复用同一个匿名 HTTP Client 和稳定 `device_id`；
- 登录成功凭证直接来自 `/account/qr_state/` 的响应 Cookie、`heyboxid` 与 `nickname`；
- Web 请求还需要持久化的 `x_xhh_tokenid`。本项目按 MIT 参考的公开形状使用当前时间、
  三段密码学随机输入、MD5 协议摘要和 Base64 独立实现，不复制无许可证项目的特殊常量；
- 删除未经参考项目或真实响应确认的 `/account/restore_login` 请求；
- 点击“生成二维码”会清除旧的认证熔断和失败计数。扫码状态临时请求失败时，后台轮询会在
  二维码有效期内退避重试，不再直接退出。

Web 通用参数现为 `os_type=web`、`app=web`、`client_type=web`、`version=999.0.4`、
`web_version=2.5`、`x_client_type=web`、`x_app=heybox_website`、
`x_os_type=Windows`、`device_info=Chrome`，并包含动态签名和稳定 `device_id`。
这些字段有 MockTransport 测试，但仍需用户真实扫码复测。

### v1.0.8 @ 通知类型修正

用户在 AstrBot 4.26.7 的真实轮询日志中确认：@ 通知接口返回 `result.messages`，原始条数
持续增加，消息类型为 `17`。v1.0.7 只接收 `16`，因此解析接收数为 0，事件在数据库认领前
被过滤。模拟运行位于后续回复阶段，切换开关无法改变这一结果。

### v1.0.9 真实接收与队列修正

AstrBot 4.26.8 的真实运行日志确认，类型 `17` 已达到“原始 7 条 / 接收 7 条”，其中三条
完成模拟运行并保存了生成回复；图片进入 AstrBot 媒体理解流程，动态上下文、当前人格和
Agent Runner 均被调用。该结果验证的是接收与生成链路，真实评论写接口仍保持待验证。

同一消息中心页面会持续返回历史通知。v1.0.9 在入队前查询 `incoming_events` 的索引状态，
并使用进程内待处理事件键过滤分页内和相邻轮询中的重复项；到期的 `retry_wait` 事件仍可
正常重新入队。此修正不改变小黑盒请求参数或回复目标。

### v1.0.10 消息边界与评论终态修正

AstrBot 4.26.8 的真实日志显示，适配器在一次启动周期中依次重新提交“测试九、测试八、
测试五、测试四、测试三、测试二、测试一下”等既有通知。小黑盒消息中心持续返回历史页，
部分响应缺少可靠创建时间；解析层为缺省时间填入当前时间后，v1.0.9 的启动时间判断无法
识别这些历史记录。

重新核对固定快照 `SomeOvO/xhhRobot@5efd9449` 后确认：

- `xhh/main.go` 明确假定消息按 `message_id` 从大到小返回，并保存上一轮最大消息 ID；
- 首次运行默认先读取当前最新消息 ID，再把已有消息写为已处理历史；
- `db/main.go` 以 `msg_id` 唯一插入，重启后从数据库恢复最新边界；
- `xhh/reply.go` 对评论响应 `status=failed` 直接标记完成，避免重复请求。

v1.0.10 采用独立 Python 实现：`notification_cursors` 按账号和通知类型保存最新
`message_id`，首次轮询默认只建立基线；新事件先写入 `incoming_events`，扫描到旧边界且
全部新事件已持久化后才推进游标。真实日志中的评论响应
`status=failed/msg=出现一点问题/code=1000` 归为明确拒绝并进入失败终态。服务端结果
不明确的网络、5xx 或响应结构异常进入 `send_unknown`，按近期机器人评论核对，发送闸门
保留已有尝试并拦截第二次 POST。

真实环境已观察到评论创建请求和 `failed/code 1000` 返回；成功响应中的评论 ID、近期评论
可见延迟和全部错误码语义继续按待验证项管理。

### v1.1.0 持久化重试与审核发送闸门

参考实现的通知获取与回复处理相互独立：`CheckAt()` 按消息 ID 写入数据库，`AutoReply()`
持续读取数据库中的未完成记录。v1.1.0 保持这一行为边界：队列或单用户上限触发时先把事件
写入 `incoming_events` 并设为 `retry_wait`，每轮轮询先从 SQLite 恢复到期重试，再拉取新
通知。通知游标推进后，待重试事件仍由数据库驱动处理。

主动审核批准增加 `sending` 中间状态、SQLite 条件更新和账号级并发锁。同一候选只允许一个
审核请求进入真实发送；更新或重启发现遗留 `sending` 时转为 `send_unknown`，交由人工核对。

### v1.1.1 AstrBot 覆盖更新恢复

AstrBot 覆盖更新会重新导入插件并注销、重新注册平台类型，已存在的平台实例由插件在
`Star.initialize()` 阶段交给 `PlatformManager.reload()` 重建。新实例继续读取原
`profile_id`、凭证、通知游标和 SQLite 状态，再恢复通知轮询；冷启动仍由 AstrBot 的平台
初始化阶段创建实例。平台注册和实例元数据均引用仓库根目录 `logo.png`。
该修正只改变本地调度和幂等逻辑，Workshop 请求路径与表单字段保持不变。

### v1.1.2 评论与原帖上下文合并

评论区 @ 先按参考项目的行为，仅使用 `link_id` 请求 `/bbs/app/link/tree`，读取原帖标题、
JSON 富文本和图片；存在根评论 ID 时，再请求指定楼层并合并评论树。通知项内嵌的 `link`
或 `post` 快照用于补全缺失字段。最终原帖背景通过临时用户内容注入，当前评论文本保留为
原生用户消息；评论图片和原帖图片交替加入 AstrBot `Image` 组件。

### v1.1.3 多图原生推理截止时间

AstrBot 4.26.8 会在主 Agent 请求前依次处理消息链中的图片。真实 6 图日志显示，图片理解已占用
约 154 秒，随后主 Agent 才开始生成回复；固定 120 秒截止时间会先把事件标记为
`dead_letter`。适配器现保留用户配置的基础回复超时，并按实际进入消息链的图片数量增加宽限：
默认图片超时 15 秒时，每张增加 30 秒，因此 6 图事件从 120 秒扩展为 300 秒。总截止时间限制
为 900 秒，超时状态会记录本事件实际采用的秒数。

### v1.2.0 推荐流与分区

2026-07-30 对 `GET /bbs/app/feeds` 进行公开只读验证，确认请求使用
`pull=0&offset=<n>&heybox_id=<uid>`，帖子数组位于 `result.links`。观察到的稳定字段包括
`linkid/title/description/create_at/user/topics/hashtags/imgs/comment_num/up`。

分区来自每条帖子的 `topics`、`hashtags` 和内容标签。当前未观察到稳定的服务端分区参数，
因此插件使用中文下拉选项在本地筛选结构化主题与标签；`All（全部）` 保留推荐流原顺序。
候选挑选支持推荐顺序、随机、最新和热门，热门值由评论数与点赞等公开计数计算。

公开行为同时与
[`k1m0206/better-XiaoHeiHe@361f4a4`](https://github.com/k1m0206/better-XiaoHeiHe/tree/361f4a4e05fc6e19c110652b1fb8c8b5837ca775)
进行接口形状交叉核对。本项目仅参考端点行为，解析、筛选和排序均为独立 Python 实现。
主动真实评论仍经过模拟运行与人工审核边界。

重新核对 `SomeOvO/xhhRobot` 的公开轮询行为与 MIT 许可的
`HadeonYu/heybox-bot@c2b5797` 后，v1.0.8 独立实现以下规则：

- 请求继续使用 `message_type=16`；该参数对应小黑盒“@我的”消息类别；
- 响应 `message_type=16` 表示帖子正文中 @，正文和帖子 ID 来自 `link`；
- 响应 `message_type=17` 表示评论中 @，使用
  `comment_a_id/comment_a_text/root_comment_id/linkid`；
- 帖子 @ 使用 `xhh_post_<post_id>`，回复目标为帖子级评论；
- 评论 @ 使用 `xhh_thread_<post_id>_<root_comment_id>`，回复目标为当前触发评论；
- 帖子 @ 缺少评论 ID 时，以 `post_message_<message_id>` 作为本地稳定去重标识，该值只用于
  本插件数据库和消息 ID；
- 其他消息类型继续由解析层过滤。

脱敏 fixture 覆盖单条 16、单条 17 和 5 条 17 批量响应。该修正来自用户真实响应类型与
许可清晰的字段说明；真实评论发送接口仍按独立验证状态管理。

## v1.0.8 参考实现复核记录

本次以 `SomeOvO/xhhRobot@5efd9449e191feece1c6e2ff5f54a1d37fdd03df` 为固定快照，
通过 GitHub API 逐项复核公开行为。该仓库用于确认交互顺序和字段关系，本项目继续采用
Python 独立实现。

| 参考文件 | 观察到的行为 | v1.0.8 对应实现 |
| --- | --- | --- |
| [`xhh/login.go`](https://github.com/SomeOvO/xhhRobot/blob/5efd9449e191feece1c6e2ff5f54a1d37fdd03df/xhh/login.go) | 请求二维码、按二维码 URL 参数轮询扫码状态、从成功响应 Cookie 取得登录身份 | `auth.py` 与 `api_client.py` 复用同一匿名异步客户端，凭证由 `CredentialStore` 原子持久化 |
| [`xhh/sendreq.go`](https://github.com/SomeOvO/xhhRobot/blob/5efd9449e191feece1c6e2ff5f54a1d37fdd03df/xhh/sendreq.go) | Web 客户端参数、稳定设备 ID、动态请求签名；评论使用 Workshop 主机 | `api_client.py` 集中添加公共参数并复用连接池；签名来自许可清晰的独立实现 |
| [`xhh/main.go`](https://github.com/SomeOvO/xhhRobot/blob/5efd9449e191feece1c6e2ff5f54a1d37fdd03df/xhh/main.go) | 用 `message_type=16`、`offset/limit/no_more` 拉取“@我的”，按消息 ID 翻页至上一轮边界；首次默认以当前最新 ID 建立历史基线 | `notification_cursors` 按账号和通知类型保存边界；解析层接收响应类型 16/17，SQLite 唯一约束负责跨重启幂等 |
| [`xhh/GetLinkInfo.go`](https://github.com/SomeOvO/xhhRobot/blob/5efd9449e191feece1c6e2ff5f54a1d37fdd03df/xhh/GetLinkInfo.go) | `/bbs/app/link/tree` 返回标题、JSON 富文本和图片 | `parsers.py` 规范化文本与图片，`ContextBuilder` 将背景作为本轮不可信临时上下文 |
| [`xhh/reply.go`](https://github.com/SomeOvO/xhhRobot/blob/5efd9449e191feece1c6e2ff5f54a1d37fdd03df/xhh/reply.go) | Workshop 评论接口使用 `is_cy/link_id/reply_id/root_id/text` 表单；`status=failed` 作为完成状态处理 | `send_comment()` 使用结构化 `RoutingTarget` 生成相同字段；明确拒绝进入失败终态，超时与不明确响应进入 `send_unknown` 核对 |
| [`xhh/owner.go`](https://github.com/SomeOvO/xhhRobot/blob/5efd9449e191feece1c6e2ff5f54a1d37fdd03df/xhh/owner.go) | 以数字 UID 进行允许列表判断 | `PermissionService` 全程按字符串 UID 处理主人、黑白名单和独立管理员映射 |
| [`xhh/start.go`](https://github.com/SomeOvO/xhhRobot/blob/5efd9449e191feece1c6e2ff5f54a1d37fdd03df/xhh/start.go) | 通知获取与回复处理分为后台任务 | `TaskManager` 管理轮询器、有界回复 worker、清理、健康检查和登录任务 |

AstrBot 适配器的推理部分采用 AstrBot 原生事件队列：通知在完成幂等、权限和上下文构建后
转换为 `AstrBotMessage`，再由当前人格、会话、Agent 和工具链生成回复。参考项目中的独立
AI 客户端不进入本项目技术边界。其特殊常量、注释、目录结构和请求代码也未作为实现来源。

## 端点清单

| 功能 | 方法与路径 | 参考观察 | 本项目状态 |
| --- | --- | --- | --- |
| 获取二维码 | `GET /account/get_qrcode_url/` | 参考项目出现该路径 | fixture 已测，待真实验证 |
| 查询扫码状态 | `GET /account/qr_state/` | 参考项目出现该路径 | fixture 已测，待真实验证 |
| 当前账号权限 | `GET /bbs/app/api/user/permission` | MIT 参考项目出现该路径 | Mock 已测，待用户复测 |
| @/回复通知 | `GET /bbs/app/user/message` | 用户日志确认 17；参考实现确认 16/17 | offset 分页、16/17 与 1/2 筛选、多形状解析已测 |
| 帖子与评论树 | `GET /bbs/app/link/tree` | 参考项目按 `link_id` 读取标题、富文本和图片 | 原帖与指定楼层双源合并 Mock 已测，字段待真实验证 |
| 创建评论 | `POST https://workshopapi.xiaoheihe.cn/bbs/app/comment/create` | 参考项目出现该主机与路径 | 表单和响应 Mock 已测，动态签名与真实限制待验证 |
| 近期评论核对 | `GET /bbs/app/comment/user` | 本项目隔离契约 | Mock 已测，路径、排序和一致性待真实验证 |
| 主动帖子流 | `GET /bbs/app/feeds` | 公开只读验证与行为交叉核对 | `result.links`、分页参数、规范化、中文分区与排序 Mock 已测 |

所有路径只存在于 `xiaoheihe/endpoints.py`，所有字段兼容处理只存在于
`xiaoheihe/parsers.py`。真实环境出现结构变化时，应新增脱敏 fixture、修改解析层并回归；
适配器和业务服务始终使用规范化模型。

## 本项目解析规范

通知规范化为：

```text
profile_id
external_event_id
external_comment_id
notification_id
event_type: mention | reply
sender_uid / sender_nickname
post_id / post_author_uid
root_comment_id / parent_comment_id
content / created_at / explicit_wake / image_urls
```

缺少 `post_id`、稳定通知标识、发送者 UID，或评论事件缺少评论 ID 时，解析层抛出
`ResponseShapeError`，API Client 转换为 `ResponseContractError`，让上游契约变化直接
进入可观测错误记录。

扫码状态映射为：

```text
idle
requesting_qr
waiting_scan
scanned_waiting_confirm
success
expired
failed
logged_out
credential_invalid
```

WebUI 只接收二维码图片、公开状态、昵称、UID 和时间；Cookie、Token、设备 ID、签名材料
永不返回。

## 请求策略

- 一个 `profile_id` 一个长生命周期客户端；匿名登录客户端与认证客户端分开；
- 统一 User-Agent、连接池、连接/读取/总超时和最小请求间隔；
- GET 等标记为 `retry_safe` 的请求使用指数退避、随机抖动和 `Retry-After`；
- 创建评论不是 `retry_safe`；
- 评论发送发生网络超时后标记 `send_unknown`；
- 只在目标帖子/楼层近期记录中确认同一登录 UID 和同一规范文本后补记成功；
- 未核对到不等于未发送。由于近期评论接口的一致性尚未实测，v1.0.0 不自动重发。

## 真实账号验证清单

发布真实发送前应逐项记录脱敏结果：

1. 二维码请求 ID、二维码内容和过期时间字段；
2. 等待扫码、已扫码待确认、成功、过期状态值；
3. 登录成功的 Cookie/Token 来源以及 UID/昵称字段；
4. @ 与回复通知分页参数、游标、创建时间单位；
5. 通知 ID、评论 ID、根评论/父评论关系；
6. 帖子标题、正文、作者、图片和完整楼层字段；
7. 评论创建所需字段、最大字符数、错误码和成功评论 ID；
8. 发送超时后的近期评论可见延迟与排序；
9. 401、403、429 和 `Retry-After` 的实际行为；
10. 主动帖子流来源、帖子类型和分页。

任何真实响应样本在提交前都必须移除 Cookie、Token、设备 ID、UID、昵称、私有帖子内容和
可追踪 URL 参数。
