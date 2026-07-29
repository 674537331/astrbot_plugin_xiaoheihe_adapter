# AstrBot 小黑盒适配器

> 将小黑盒帖子与评论接入 AstrBot 原生会话、人格和 Agent 链路。

[![CI](https://github.com/674537331/astrbot_plugin_xiaoheihe_adapter/actions/workflows/ci.yml/badge.svg)](https://github.com/674537331/astrbot_plugin_xiaoheihe_adapter/actions/workflows/ci.yml)
[![CodeQL](https://github.com/674537331/astrbot_plugin_xiaoheihe_adapter/actions/workflows/codeql.yml/badge.svg)](https://github.com/674537331/astrbot_plugin_xiaoheihe_adapter/actions/workflows/codeql.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

`astrbot_plugin_xiaoheihe_adapter` 是一个 AstrBot 原生平台适配器。小黑盒的 @ 通知和对机器人
评论的直接回复会被规范化为 `AstrBotMessage`，通过 `commit_event()` 进入 AstrBot 正常消息
管线，再由当前模型提供商、人格、原生会话、长期记忆、Agent Runner、MCP、Skills、Web
Search、视觉模型以及获准的插件和工具处理。

模型调用统一交给 AstrBot 当前提供商、人格、会话和 Agent 链路；登录与管理操作集中在
Plugin Page。

## 当前版本

当前发布版本为 **v1.0.9**。本版本在 v1.0.8 已接收帖子 @ 类型 `16` 与评论 @ 类型 `17`
的基础上，修复历史通知反复占用待处理队列的问题，并将管理页中的运行保护模式统一命名为
“模拟运行”。每次版本发布均同步更新本 README 与 [CHANGELOG](CHANGELOG.md)，使安装步骤、
功能边界和故障排查与实际代码保持一致。

## 风险提示

小黑盒相关接口属于非公开、可能变化的客户端接口。v1.0.0 的网络契约基于公开参考项目的
功能行为独立实现，当前自动化验证范围为脱敏 fixture 和 Mock HTTP 测试，**真实账号验证
状态以 API 契约文档逐项记录为准**。

- 首次使用必须保持模拟运行开启（`dry_run: true`）；
- 主动刷帖默认关闭，且即使启用也默认只生成待审核候选；
- 真实评论路径、参数、签名、返回字段、字符限制和近期评论一致性仍待真实环境验证；
- 使用自动化可能触发平台限制，使用者需要自行确认平台规则并承担账号风险；
- 验证码、平台风控与账号安全校验均由小黑盒官方客户端处理。

准确边界见 [小黑盒 API 契约](docs/xiaoheihe-api-contract.md)。

## 核心特性

- 原生平台类型 `xiaoheihe`，可在“机器人 → 新增适配器”创建；
- 登录与账号管理统一在 Plugin Page 完成；
- @ 与直接回复进入 AstrBot 原生事件队列；
- 同时规范化帖子 @（`message_type=16`）和评论 @（`message_type=17`）；
- “帖子 + 根楼层”确定性会话隔离；
- 帖子、楼层、作者、引用和图片作为本轮不可信临时上下文；
- SQLite 事务幂等、唯一约束、事件状态机和重启恢复；
- 登录 UID、外部评论 ID、发送记录、内容哈希和数据库约束共同防止自循环；
- 模拟运行完成整个原生推理链路，并将真实评论接口保持停用；
- 主动帖子默认关闭、默认模拟运行、默认人工审核；
- 401/403 熔断、429 `Retry-After`、安全重试和 `send_unknown` 核对；
- 一个账号一个长生命周期异步 HTTP Client；
- 有界队列、单用户上限、同楼层串行和有限并行；
- 管理页提供状态、登录、分组可视化设置、事件、审核、日志、SSE 和存储管理；
- 凭证原子保存、日志/诊断脱敏和安全退出。

## 环境要求

- AstrBot `>=4.24.2,<5`；
- 重点兼容 AstrBot 4.26.2；
- 已对 2026-07-28 的当前稳定版 AstrBot 4.26.7 做包级 API 契约检查；
- Python 3.12–3.14（AstrBot 4.26.2 要求 Python 3.12+）；
- AstrBot 运行环境能够安装 `requirements.txt` 中的依赖；
- 可以访问小黑盒所需 HTTPS 域名。

适配器运行时 API 由 AstrBot 提供，本仓库保持独立插件边界。版本接口结论见
[兼容性说明](docs/compatibility.md)。

AstrBot 4.24.2 可加载核心适配器；扫码登录和完整管理页使用 AstrBot 4.26.2 起提供的公开
Plugin Page API。

## 安装

### 从 GitHub 安装

在 AstrBot WebUI 的插件市场或插件安装界面中使用仓库地址：

```text
https://github.com/674537331/astrbot_plugin_xiaoheihe_adapter
```

AstrBot 会按 `requirements.txt` 安装 `aiosqlite`、`httpx` 和 `qrcode`。安装完成后启用插件。

### 手动安装

将仓库目录放入 AstrBot 的插件目录，目录名保持
`astrbot_plugin_xiaoheihe_adapter`，安装依赖后重载插件：

```bash
python -m pip install -r requirements.txt
```

不要把 `data/`、凭证、数据库、日志或真实二维码复制进插件目录。

## 快速上手

```text
安装并启用插件
  → 打开插件详情页的“小黑盒管理”
  → 选择 profile_id 并扫码登录
  → 在“机器人 → 新增适配器”选择“小黑盒”
  → 适配器 profile_id 选择同一档案
  → 保持模拟运行，观察事件与生成结果
  → 完成真实接口验证后再关闭模拟运行
```

### 1. 扫码登录

打开插件详情页中的“小黑盒管理”：

1. 在“扫码登录”选择账号档案；
2. 点击“生成二维码”；
3. 使用小黑盒客户端扫码并确认；
4. 点击“检查登录”，直到显示 `success`；
5. 核对昵称、UID、登录时间和最近检查时间。

管理页响应限定为二维码图片、公开登录状态、昵称、UID 和时间。二维码有倒计时；过期后
重新生成。

登录技术路线与 `SomeOvO/xhhRobot` 观察到的交互顺序保持一致：

```text
获取二维码
  → 使用同一匿名 HTTP Client 轮询 qr_state
  → 从成功响应与 Cookie 会话提取 UID 和凭证
  → 持久化稳定 device_id 与 Web 客户端身份
  → 为后续请求生成时间参数、nonce 和 hkey
  → 启动账号检查与消息通知轮询
```

本项目使用 Python 独立实现。身份参数形状参考具备 MIT 许可证的
`HadeonYu/heybox-bot`，许可声明记录在 [THIRD_PARTY_NOTICES](THIRD_PARTY_NOTICES.md)。

### 2. 创建小黑盒适配器

进入“机器人 → 新增适配器”，选择“小黑盒”，填写：

| 字段 | 说明 |
| --- | --- |
| `id` | AstrBot 平台实例 ID；同一实例内唯一 |
| `enable` | 是否启用该实例 |
| `profile_id` | 管理页中已登录的账号档案 ID |

`profile_id` 始终在机器人/适配器页面修改，插件配置则通过公开 `AstrBotConfig` 保存接口
管理；两类配置各自使用 AstrBot 的标准入口。

### 3. 模拟运行验证

默认账号档案已启用模拟运行，对应配置键 `dry_run: true`。此模式会执行：

- 获取和解析通知；
- 数据库幂等；
- 权限与自身消息过滤；
- 帖子、楼层和图片上下文；
- AstrBot 原生人格、会话、记忆、Agent 和工具链；
- 回复聚合、脱敏、空内容检查和长度限制；
- 保存生成结果与耗时状态。

生成结果会保存为“模拟运行”记录，真实评论接口保持停用。默认
`dry_run_mark_processed: true`，相同通知只执行一次模型推理。在事件记录中确认 session、
目标楼层、上下文、图片和回复均正确，再考虑真实启用。

## 配置说明

配置只有两个来源层级，不重复保存：

1. 机器人/适配器配置：`id`、`enable`、`profile_id`；
2. 插件 `AstrBotConfig`：账号档案和全部运行设置。

Plugin Page 设置页和 AstrBot 原生插件设置操作同一个 `AstrBotConfig` 对象。保存时后端重新
校验并调用 `save_config()`；失败会回滚内存配置并显示错误。保存成功后只安全刷新受影响的
后台服务，不重启整个 AstrBot。

v1.0.9 的设置页按账号档案、通知轮询、上下文与图片、回复策略、身份过滤、网络并发、主动
帖子、数据保留和日志分组展示。布尔值使用开关，数字使用数值输入，日志等级使用下拉框，
名单与关键词使用“每行一项”的列表输入。点击“恢复默认”只把默认值载入表单，再点击
“校验并保存”后生效。

### 账号档案

v1.0.0 正式支持一个运行账号，结构已按 `profile_id` 为多账号扩展：

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `profile_id` | `default` | 账号档案 ID，只允许字母、数字、`_`、`-` |
| `display_name` | `默认账号` | 管理页显示名 |
| `enabled` | `true` | 是否启用档案 |
| `poll_mentions` | `true` | 轮询 @ 通知 |
| `poll_replies` | `true` | 轮询直接回复 |
| `dry_run` | `true` | 模拟运行：仅生成并保存回复，真实评论保持停用 |
| `owner_uid` | 空 | 主人小黑盒数字 UID，按字符串处理 |

### 关键默认值

| 分组 | 字段 | 默认值 |
| --- | --- | --- |
| 轮询 | `poll_interval_seconds` | `60`，后端硬限制不低于 30 秒 |
| 轮询 | `max_pages_per_poll` / `initial_backfill_count` | `3` / `0` |
| 上下文 | `context_cache_ttl_seconds` / 最大条目 | `300` / `256` |
| 图片 | `enable_image_understanding` | `true` |
| 图片 | 每事件最大图片数 | `6` |
| 回复 | `max_reply_chars` / `reply_timeout_seconds` | `500` / `120` |
| 网络 | `min_request_interval_seconds` | `1.0` |
| 并发 | worker / 总积压 / 单用户积压 | `2` / `50` / `5` |
| 重试 | `max_retries` | `3`，评论 POST 不盲重试 |
| 主动帖子 | 启用 / 模拟运行 / 审核 | `false` / `true` / `true` |
| 主动帖子 | 间隔 / 抖动 / 每轮 / 每日 | `900` / `60` / `1` / `10` |

完整字段、类型、边界与中文说明位于 `_conf_schema.json`。

## 会话映射

小黑盒评论区以“帖子 + 根评论楼层”隔离：

```text
group_id             xhh_post_<post_id>
帖子 session_id      xhh_post_<post_id>
楼层 session_id      xhh_thread_<post_id>_<root_comment_id>
message_id           xhh_<event_type>_<notification_id>_<comment_id>
```

同一根楼层始终得到同一 session；不同帖子和不同根楼层使用独立上下文。映射可从外部 ID
重新计算，即使删除插件本地 `session_mappings` 记录也保持结果一致。

`send_by_session()` 会严格解析上述格式，仅在完整恢复目标帖子和楼层后发送；目标信息缺失
时返回明确错误。帖子 ID 或根评论 ID 含下划线时按最后一个下划线分割楼层路由；真实环境
启用前应确认小黑盒 ID 字符集。

## 上下文与 Prompt 安全

当前评论正文是原生用户消息。帖子标题、正文、作者、父/根评论、完整楼层和必要图片由
`on_llm_request` 注入：

```xml
<xiaoheihe_context trust="untrusted">
  ...
</xiaoheihe_context>
```

该内容使用 `TextPart.mark_as_temp()`，仅属于本轮用户侧临时上下文：

- system prompt 保持由 AstrBot 当前人格管理；
- 完整帖子只作为本轮临时上下文；
- 人格与安全规则保持最高优先级；
- 清理 HTML、控制字符、表情占位、重复行、追踪参数和异常连续字符。

## 图片理解

v1.0.0 的重点是接收并理解图片。经过校验和去重的公开 HTTPS 图片 URL 被转换为 AstrBot
原生 `Image` 组件，由核心媒体和视觉模型流程处理。

- 默认每事件最多 6 张；
- 拒绝 `file:`、HTTP 明文、带认证信息、localhost、明显内网和保留地址；
- 域名在提交媒体组件前解析，任一 DNS 结果为内网或保留地址时拒绝；
- 图片以 URL 交给 AstrBot 核心媒体流程，插件本地图片缓存保持为空；
- 当前提供商明确声明仅支持文本时，插件会移除本轮图片、保留文本，并在管理页显示降级提示；
- 提供商未声明 `modalities` 时遵循 AstrBot 的向后兼容语义，由核心媒体流程决定能力。

AstrBot 实际抓取时仍应再次校验最终 DNS 地址和重定向链，降低校验与抓取之间 DNS 重绑定的
时间窗口。若未来在插件内增加下载，必须同时落实 Content-Type、文件头、大小、总量、
重定向和官方临时文件跟踪。

当前图片能力范围为接收和理解。评论区图片上传列为待真实账号验证功能。

## 白名单、黑名单与主人 UID

固定优先级：

```text
机器人自身 → 黑名单 → 主人 UID → 白名单 → 普通触发规则
```

支持用户 UID、帖子作者 UID、关键词的白/黑名单。身份只按字符串 UID 判断，不使用昵称。
主人可以绕过普通白名单并获得队列高优先级，但仍受硬上限。

主人 UID 与 AstrBot 管理员是两套独立身份。`map_owner_to_astrbot_admin` 是独立开关且
默认 `false`。普通小黑盒用户保持普通权限；高风险工具的最终权限由 AstrBot 和已安装插件
决定。

## 防止机器人自我循环

插件同时使用登录账号 UID、通知发送者 UID、外部评论 ID、本地发送记录、内容哈希、目标
帖子/楼层、进程内积压集合和 SQLite 唯一约束。机器人发出的评论重新出现在通知时会被
忽略；身份判断使用数字 UID、外部评论 ID、发送记录与目标路由等结构化信息。

## 回复稳定性

- 合并最终 `Plain` 文本段，空回复不发送；
- 删除调试/工具片段、异常堆栈线索和敏感认证文本；
- 按 Unicode 字符安全截断；
- 平台使用单条最终评论，事件层会先聚合所有流式片段；
- 同一入站事件用锁保证最多一条真实评论；
- 评论 POST 不参与网络自动重试；
- 超时或连接中断进入 `send_unknown`；
- 只在同一帖子/楼层、同一机器人 UID、同一规范文本的近期评论中核对成功；
- 核对不到时进入人工检查，不自动补发。

默认 500 字只是保守配置，实际平台限制待真实验证后调整。

## 主动刷帖审核

这是高风险能力，默认：

```yaml
enabled: false
dry_run: true
review_required: true
interval_seconds: 900
max_per_run: 1
max_per_day: 10
```

启用后帖子先经过空正文、广告、推广、抽奖、交易、敏感/引战类型和关键词过滤，再创建
`proactive_feed` 合成事件进入同一 AstrBot 原生管线。最终文本先保存为待审核候选。

管理页可以编辑、批准、拒绝和批量拒绝过期候选。批准使用当前已审核文本，不重新调用模型。
真实发送只有在主动配置关闭模拟运行、候选通过人工审核且未超每日上限时发生。

由于帖子流和评论端点仍待真实验证，建议 v1.0.0 保持该功能关闭。

## 数据目录与 SQLite

运行数据由 `StarTools.get_data_dir()` 定位，标准部署通常是：

```text
data/plugin_data/astrbot_plugin_xiaoheihe_adapter/
├── credentials/
│   └── <profile_id>.json
├── logs/
└── xiaoheihe.db
```

SQLite 作为独立嵌入式存储运行。数据库启用 WAL、外键、busy timeout、NORMAL synchronous
和增量回收。

迁移版本：`3`（v3 增加持久化重试计数、下次重试时间和索引）。表包括：

- `schema_migrations`
- `account_state`
- `incoming_events`
- `processed_event_keys`
- `outgoing_replies`
- `self_comment_ids`
- `session_mappings`
- `feed_candidates`
- `runtime_errors`
- `daily_counters`

`profile_id + external_event_id` 和 `profile_id + external_comment_id` 都有唯一约束。

## 自动清理与存储上限

启动 60 秒后清理一次，此后每 24 小时执行；每类每批最多 500 条，不在每次轮询时清理，
不执行频繁完整 `VACUUM`。默认：

- 通知正文、模拟运行、失败和运行错误：30 天；
- 成功回复正文：90 天；
- 会话映射：180 天；
- 轻量去重键：365 天；
- 自身评论 ID、账号状态和每日统计：长期保留；
- 数据库警告/软上限：150 MB / 200 MB；
- 日志总上限：100 MB；
- 图片缓存软上限：200 MB（当前 URL 直传模式下保持为空）。

正文到期后仍保留轻量去重键。清理范围限定为插件自身数据库和日志，AstrBot 原生会话
数据库保持完整。管理页支持清理预览和显式确认后的安全清理。

超过数据库软上限后，每轮按最多 500 条依次处理已拒绝/过期候选、模拟运行正文、历史终态
通知正文和成功回复正文。待处理、待重试、未审核候选、自身评论记录、账号状态和必要去重键
始终完整保留。

## 凭证安全

- 每个 `profile_id` 独立 JSON 文件；
- 临时文件写入并 `fsync` 后原子替换；
- POSIX 目录尽量 `0700`、文件尽量 `0600`；
- Cookie、Token、设备 ID 和签名键不写入 AstrBotConfig；
- WebUI、日志、错误详情和诊断自动脱敏；
- 安全退出仅删除选中档案的凭证，并尽力覆盖小文件；
- 更新和重装插件不得覆盖插件数据目录；
- 凭证失效后停止真实发送并触发熔断。

Windows 无 POSIX 权限位，请使用 AstrBot 运行账号的 NTFS ACL 保护整个
`data/plugin_data/astrbot_plugin_xiaoheihe_adapter/`，不要让其他本地用户读取。

## 日志与错误提醒

管理页提供脱敏结构化日志、级别/关键词筛选、SSE 实时刷新、自动重连和关闭页面时取消订阅。
内存和订阅队列均有界，日志按总大小轮转。

顶部告警展示登录失效、401/403 熔断、数据库健康、存储阈值和后台状态。API 响应结构变化会
以 `response_shape` 错误记录，便于生成脱敏诊断。

## 常见故障

### 新增适配器列表未显示“小黑盒”

确认插件已启用，`main.py` 能导入 `xiaoheihe.adapter`，然后重载插件并刷新机器人页面。
检查 AstrBot 版本是否在支持范围内。

### 管理页打不开或 API 不匹配

确认 AstrBot 版本支持 Plugin Pages；刷新插件详情页。后端路由必须带插件名前缀，页面调用
必须使用不带前缀的相对 endpoint。不要在独立浏览器地址中直接打开 `index.html`。

### 扫码后一直等待

请确认插件版本至少为 v1.0.7，然后重新生成二维码。小黑盒客户端显示“登录加速器”是
当前二维码入口的客户端文案；在手机端确认后，管理页应在下一次自动轮询或点击“检查登录”
时完成登录。若仍然等待，请复制管理页的脱敏诊断信息；不要发送 Cookie 或二维码原始 URL。

v1.0.0 未兼容参考登录响应的 `result.error = "ok"` 成功标记；v1.0.1 仍假定 UID 来自
`Set-Cookie`。v1.0.2 已兼容 JSON 中的 `heyboxid`、`pkey` 和 `account_detail.userid`，
但其额外 `restore_login` 假设缺少真实依据。v1.0.7 已改为只使用扫码状态响应与同一
Cookie 会话，并为二维码请求补齐稳定 Web 客户端身份。请升级，不要继续使用旧二维码。

### 管理页提示缺少 `plugin_tag`

如果管理页顶部出现 `Formatting field not found in record: 'plugin_tag'`，请升级到
v1.0.3。旧版插件文件日志会直接传播到 AstrBot 根格式器，导致状态接口在记录日志时中断；
v1.0.3 已隔离文件日志并通过 AstrBot 公共 logger 输出控制台日志。

### 通知轮询提示 `响应 data/result 应为对象`

请升级到 v1.0.4。旧版使用了未经真实验证的 `type=at/reply&page=` 参数，和小黑盒消息接口
实际使用的 `message_type/list_type/offset` 不一致。升级并热重载后，请使用另一个账号重新
发送一条新的 @；首次运行默认只接收启动时刻之后的新通知。

### 小黑盒“@我的”已有通知，但事件记录仍为 0

请升级到 v1.0.8 或更高版本，并保持模拟运行。状态总览会显示“@通知 原始/接收”，运行日志会在响应
结构或消息类型变化时记录一次只包含字段名、数量和类型的安全摘要。小黑盒消息中心的
`message_type=16` 表示帖子中 @，`message_type=17` 表示评论中 @；v1.0.8 两者都会进入
事件记录和 AstrBot 原生管线。若“原始”为 0，请先确认网页登录态与通知可见性；若升级后
仍出现“原始”大于 0 而“接收”为 0，请复制脱敏诊断用于补充解析 fixture。Plugin Page 的
Clipboard API 权限受限时，页面会自动显示只读文本框，可长按或全选后手动复制。

### 提示 `relogin: 请重新登录`

v1.0.5 首次正确识别出小黑盒 HTTP 200 响应中的 `relogin`，同时证明此前的“成功空轮询”
不是真正成功。请升级到 v1.0.6；该版本补齐动态 `hkey`，并在仍收到 `relogin` 时立即把
账号标为 `credential_invalid`、停止轮询和真实发送。若 v1.0.6 重新扫码后仍显示
`status=failed`，请升级到 v1.0.7：该版本补齐二维码阶段的统一 Web 参数、稳定
`device_id` 与 `x_xhh_tokenid`，并删除未经验证的恢复登录接口。升级后点击“生成二维码”
会清除旧熔断；只需扫描最新二维码并等待自动检查，不要连续点击“检查登录”进行高频试探。

### 后台任务退出：xhh-cleanup

v1.0.1 起，自动清理遇到临时 SQLite 锁或维护错误会记录脱敏原因并在一小时后重试，不再
永久退出。升级并热重载插件后旧的任务失败提示会清除；如果新版本仍提示失败，管理页会同时
显示具体的脱敏错误原因。

### HTTP 401

凭证失效。适配器会停止真实发送并打开熔断。使用管理页安全退出，重新扫码，点击“检查
登录”确认 `success` 后再观察模拟运行。

### HTTP 403

可能是接口权限、账号状态或请求契约变化。不要高频重试，不要尝试绕过风控。保持模拟运行，
等待熔断，核对脱敏诊断和 [API 契约](docs/xiaoheihe-api-contract.md)。

### 日志反复提示单用户待处理事件达到上限

v1.0.8 首次接收类型 `17` 后，会把升级前仍显示在消息中心的历史 @ 作为一次性积压处理；
事件推理期间可能短暂达到默认单用户上限 5。v1.0.9 在入队前检查 SQLite 状态并维护进程内
待处理事件键，已完成和处理中通知不再反复占位。其他插件尝试追加表情或第二段消息时，
适配器按单评论策略忽略额外消息段，并以调试级提示记录。

### HTTP 429

尊重 `Retry-After`，增大轮询间隔和最小请求间隔，减少分页与并发。不要通过多设备或伪造
指纹规避限流。

### API 响应结构变化

保存脱敏响应形状，不保存真实正文或凭证；新增 fixture，只修改 `endpoints.py` /
`parsers.py`，运行全部测试。不要在业务层临时散落字段兼容。

### 图片理解未生效

确认图片是公开 HTTPS URL、未指向内网，当前模型支持视觉且 AstrBot 媒体流程可访问该域名。
视觉不可用时插件按文本降级，不应永久卡住事件。

## 开发与测试

```bash
python -m pip install -e ".[test]"
ruff check .
ruff format --check .
python -m coverage run -m pytest -q
python -m coverage report
python -m compileall -q .
```

普通测试使用 Mock HTTP，不访问真实账号接口。当前本地执行结果和测试边界见
[测试说明](docs/testing.md)。GitHub 仓库永久配置 CI、CodeQL、Dependency Review、
Gitleaks 和 Dependabot。

建议为 `main` 开启分支保护：

- 禁止直接推送和 force push；
- PR 必须通过 CI；
- CodeQL 不得有高危结果；
- 至少一次人工审查。

## 隐私与安全

小黑盒通知和评论可能包含个人信息。仅保留排错与幂等所需数据，按保留策略清理正文；分享
诊断前仍应人工检查。不要公开数据库、日志、二维码或凭证文件。

安全问题请参阅 [SECURITY.md](SECURITY.md)，贡献流程参阅
[CONTRIBUTING.md](CONTRIBUTING.md)。

## 已知限制

- 所有小黑盒真实网络接口仍待独立账号验证；
- v1.0.0 正式运行支持一个账号，多档案仅提供隔离基础；
- 图片发送能力范围为文本评论，评论图片上传列为待验证功能；
- 入站图片使用 URL 直传，DNS 重绑定和重定向链由 AstrBot 核心媒体流程继续防护；
- Plugin Page 不修改机器人适配器实例的 `profile_id`；
- 主动帖子来源和审核后真实发送仍建议保持关闭；
- AstrBot 4.24.2/4.26.2/4.26.7 的包级契约由 CI 检查，完整服务端到端登录验收状态单独记录。

## 致谢

- [AstrBot](https://github.com/AstrBotDevs/AstrBot)：平台适配器和原生 Agent 管线；
- [SomeOvO/xhhRobot](https://github.com/SomeOvO/xhhRobot)：用于理解登录、通知、帖子和评论的
  功能行为；本项目在其许可证边界下使用 Python 独立实现；
- [XiaHouSheng/heybox-core](https://github.com/XiaHouSheng/heybox-core)：依据其 MIT 许可
  独立移植小黑盒动态 `hkey` 行为；版权和许可全文见
  [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)；
- [HadeonYu/heybox-bot](https://github.com/HadeonYu/heybox-bot)：依据其 MIT 许可核对
  Web 登录参数、稳定客户端身份与 `x_xhh_tokenid` 数据形状；版权和许可全文见
  [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)；
- [674537331/astrbot_plugin_mihome](https://github.com/674537331/astrbot_plugin_mihome)：
  README 信息组织与中文说明风格参考。

## License

[MIT License](LICENSE) © RyanVaderAn。第三方组件声明见
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
