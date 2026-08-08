# AstrBot 小黑盒适配器

> 将小黑盒 @、评论回复和帖子上下文接入 AstrBot 原生会话、人格与 Agent 链路。

[![CI](https://github.com/674537331/astrbot_plugin_xiaoheihe_adapter/actions/workflows/ci.yml/badge.svg)](https://github.com/674537331/astrbot_plugin_xiaoheihe_adapter/actions/workflows/ci.yml)
[![CodeQL](https://github.com/674537331/astrbot_plugin_xiaoheihe_adapter/actions/workflows/codeql.yml/badge.svg)](https://github.com/674537331/astrbot_plugin_xiaoheihe_adapter/actions/workflows/codeql.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

当前版本：**v1.2.12**

小黑盒通知会转换为 `AstrBotMessage`，通过 `commit_event()` 进入 AstrBot 原生事件队列。回复
继续使用当前 AstrBot 模型、人格、会话历史、记忆、Agent、MCP、Skills、Web Search 和已授权
工具。插件只负责平台接入，不单独配置模型接口。

v1.2.12 重点：

- 评论回复/@ 按“当前消息 > 直接回复对象 > 最近楼层 > 原帖背景”组织上下文，歪楼后不再强行把当前话题拉回原帖；
- 被动楼层回复会实际缩小背景体积：原帖默认最多 1600 字、最近楼层默认 12 条，直接回复对象独立保留；
- 当前消息在临时背景尾部增加定位副本，并追加可信焦点规则；原生用户消息仍是唯一持久化的正文，不重复污染会话历史；
- 主动刷帖不使用上述缩减预算，继续以完整原帖为主要话题；帖子级触发也继续以原帖作为主要背景；
- 新增 `context.thread_reply_post_chars` / `thread_reply_recent_comments`，可按账号使用习惯调整被动回复背景预算；
- 同一帖子/楼层仍作为一个共享 AstrBot 会话，不按用户拆散公开讨论的上下文；
- 每一轮小黑盒用户消息都会将真实发送者 UID 写入可持久化的 LLM 用户历史，避免 A、B、C 在历史里都只剩匿名 `role=user`；
- 当前轮可信运行时元数据同步标注触发发言人 UID，并要求不同 UID 的“我/我的”等第一人称分别归属各自发言人；
- 完整帖子、楼层和评论背景继续作为临时上下文，不会随新的身份标签一起永久重复写入会话；
- 带图小黑盒消息调用 `grok_web_search` 进行普通网页查询时，不再让 Grok 插件自动重复抓取原图而把搜索带偏成识图；
- Grok 查询期间只临时隔离事件原图，返回后立即恢复；QQ 等其他平台、其他 AstrBot 工具均不受影响；
- 明确“搜这张图”“识图”“图片出处”等图片搜索仍保留原图能力，避免修复普通搜索时破坏图片搜索；
- 仅在真正调用 `grok_web_search` 时给该次查询追加直接回答约束；未安装 Grok、未调用 Grok 或使用其他工具时不修改 Prompt、图片和回复流程；
- 跟踪 AstrBot Agent 的完整运行周期，工具状态和“正在查询”等中间消息不会提前结束小黑盒回复；
- 无论本轮是否调用工具，均等待 Agent 最终结果后一次性提交评论；
- Grok 等多步工具调用、AstrBot 自带分段回复和流式回复统一聚合，最终答案不会只剩第一段；
- 插件先返回业务结果、随后继续调用 LLM 时，内容会暂存、去重并与最终回复合并，最终回复优先；
- 主动刷帖允许同时关闭模拟运行和人工审核，由 AI 生成回复后直接真实发表评论；
- 修复该配置组合触发校验异常并导致整个插件加载失败的问题；
- 模拟运行、人工审核和无审核直发现在按两个独立开关准确分流，默认安全设置保持不变；
- 多图事件按实际图片数量自动增加视觉处理时间，默认 6 图事件由 120 秒扩展为 300 秒；
- 设置保存后显示成功提示，并注明配置是否产生变化；
- 评论区 @ 会同时读取当前评论与原帖的文本、图片，再合并指定楼层；
- AstrBot 分段回复会在清理前保存完整文本，任意段数及 Agent 多步工具调用均一次性提交小黑盒评论；
- 覆盖更新或插件热重载后自动重建已启用的小黑盒适配器实例；
- 平台适配器使用仓库 `logo.png` 展示图标；
- 首次轮询按小黑盒 `message_id` 建立持久化历史基线；
- 新通知先写入 SQLite，再进入有界处理队列，拥塞事件按到期时间恢复；
- 同一入站事件使用数据库发送记录和事件级闸门限制为一次评论提交；
- 主动审核使用原子发送状态和账号级并发闸门；
- 主动回复审核发送会回写原主动事件，事件记录展示最终发送状态和实际处理完成时间；
- 主动额度只在合格帖子提交给 AI 生成回复时消耗，浏览和本地过滤不计数；
- 管理页事件类型、状态和筛选项使用中文，所有时间统一显示为上海时区的正常日期时间；
- LLM 上下文严格区分作者内容时间、插件发现时间和 AI 处理时间，并禁止将系统时间归因给作者；
- 主动浏览明确标注没有新评论触发，楼层历史评论逐条携带发布时间；
- 回复上下文同时提供原帖发布时间、触发回复时间和当前处理时间；
- 修复主动推荐流错误传递 `source` 参数导致的 `pull 参数不正确 (0/1)`；
- 推荐流可选择全部、PC 游戏、手机游戏、主机游戏、数码科技、动漫二次元等中文分区；
- 数据库迁移会自动补齐主动候选与原事件的关联；
- 可从 AstrBot 已配置 Provider 下拉框中为小黑盒事件固定 LLM Provider，并单独指定图片理解 Provider；
- 覆盖更新保留账号凭证、SQLite、配置和通知游标。
- 管理页按需加载隐藏标签内容，并只在日志页保持 SSE 连接，降低空闲请求、数据库查询和页面占用；
- 同一账号的并发首次请求只创建一个长生命周期 HTTP 客户端，不重复读取凭证或创建连接池；
- 发布链路统一使用 UTF-8，远端仓库、元数据和 Release 正文不再出现中文乱码。

## 核心特性

- 原生平台类型 `xiaoheihe`；
- Plugin Page 扫码登录和中文管理页；
- @ 与直接回复进入 AstrBot 原生消息管线；
- 当前评论、原帖、根楼层、引用、作者和双方图片作为本轮临时上下文；
- “帖子 + 根楼层”确定性会话隔离；
- 楼层会话共享上下文，但每轮 LLM 历史持久化真实发言人 UID；
- 楼层回复使用当前消息/直接回复对象优先的焦点路由，并与主动刷帖采用不同上下文预算；
- SQLite 幂等、状态机、迁移、恢复和自动清理；
- 模拟运行完成完整推理并保存结果；
- 白名单、黑名单、主人 UID 和自身消息过滤；
- 主动刷帖默认关闭，启用后默认模拟运行并要求人工审核，也可显式开启无审核真实发送；
- 401/403 熔断、429 退避、发送未知核对和脱敏日志；
- 一个账号一个长生命周期异步 HTTP Client；
- 分组可视化设置、事件记录、审核、日志和存储管理。

## 风险提示

小黑盒相关接口属于可能变化的客户端接口。项目根据公开参考项目的功能行为使用 Python 独立
实现，自动化测试采用脱敏 fixture 和 Mock HTTP。

首次使用请保持“模拟运行”开启。待真实账号验证项包括：

- 评论发送成功响应中的评论 ID；
- 平台实际字符限制；
- 评论图片上传接口；
- 发送超时后的近期评论一致性查询。

接口验证状态见 [小黑盒 API 契约](docs/xiaoheihe-api-contract.md)。自动化使用请遵守平台规则，
验证码、风控与账号安全校验由小黑盒官方客户端处理。

## 环境要求

- AstrBot `>=4.24.2,<5`；
- 重点兼容 AstrBot 4.26.2；
- Python 3.12–3.14；
- 可访问小黑盒所需 HTTPS 域名。

AstrBot 4.24.2 可加载核心适配器；扫码登录和完整管理页建议使用 AstrBot 4.26.2 或更新版本。
详细接口结论见 [兼容性说明](docs/compatibility.md)。

## 安装

在 AstrBot 插件市场或插件安装页使用仓库地址：

```text
https://github.com/674537331/astrbot_plugin_xiaoheihe_adapter
```

手动部署时，将仓库放入 AstrBot 插件目录并安装依赖：

```bash
python -m pip install -r requirements.txt
```

AstrBot 会从 `requirements.txt` 安装 `aiosqlite`、`httpx` 和 `qrcode`。

## 快速上手

```text
安装并启用插件
  → 打开插件详情页的“小黑盒管理”
  → 选择 profile_id 并扫码登录
  → 在“机器人 → 新增适配器”选择“小黑盒”
  → 适配器绑定同一个 profile_id
  → 保持模拟运行并建立通知历史基线
  → 使用另一个小黑盒账号发送一条全新的 @
  → 在事件记录中核对上下文和生成回复
  → 完成真实发送检查后关闭模拟运行
```

### 1. 扫码登录

1. 打开“插件 → 小黑盒适配器 → 小黑盒管理”；
2. 在“扫码登录”选择账号档案；
3. 点击“生成二维码”；
4. 使用小黑盒客户端扫码并确认；
5. 点击“检查登录”，直到状态显示 `success`；
6. 核对昵称、UID、登录时间和最近检查时间。

凭证保存在插件数据目录，管理页只显示登录状态、昵称、UID 和时间。

### 2. 创建适配器

进入“机器人 → 新增适配器”，选择“小黑盒”：

| 字段 | 用途 |
| --- | --- |
| `id` | AstrBot 平台实例 ID |
| `enable` | 启用该实例 |
| `profile_id` | 绑定管理页中的账号档案 |

`profile_id` 在机器人/适配器页面维护。其他运行设置在“小黑盒管理 → 设置”维护，管理页和
AstrBot 原生插件设置共用同一个 `AstrBotConfig`。

### 3. 建立通知基线

首次成功轮询会分别记录 `mention` 和 `reply` 的当前最新 `message_id`。默认
`initial_backfill_count: 0`，当前消息中心内容作为历史基线，随后产生的新通知进入处理队列。

请等待运行日志出现：

```text
mention 通知历史基线已建立
reply 通知历史基线已建立
```

然后使用另一个账号发布一条全新的 @。升级 v1.2.2 后也建议先等待该日志，再开始验证。

### 4. 模拟运行验证

“模拟运行”对应配置键 `dry_run: true`，会执行通知解析、幂等、权限过滤、上下文构建、图片
处理和 AstrBot 原生推理，并把最终回复保存到事件记录；评论发送步骤改为记录模拟结果。

`dry_run_mark_processed: true` 表示模拟结果保存成功后，将该通知记为已完成。相同通知后续
轮询直接读取完成状态，从而节省模型额度。

建议核对：

1. 事件记录只出现刚发布的新 @；
2. 相同通知只出现一条记录；
3. 帖子 ID、根楼层和发送者 UID 正确；
4. 图片、帖子正文和楼层引用进入本轮上下文；
5. 生成回复符合当前 AstrBot 人格。

## 常用设置

管理页按账号、轮询、模型 Provider、上下文与图片、回复策略、身份过滤、网络并发、主动帖子、
数据保留和日志分组展示。保存时后端校验并调用 `AstrBotConfig.save_config()`，受影响的后台
任务会安全刷新。

| 设置 | 默认值 | 说明 |
| --- | --- | --- |
| `profiles[].dry_run` | `true` | 模拟运行 |
| `polling.poll_interval_seconds` | `60` | 通知轮询间隔，最低 30 秒 |
| `polling.max_pages_per_poll` | `3` | 每类通知每轮最大页数 |
| `polling.initial_backfill_count` | `0` | 首次基线后回溯条数 |
| `providers.llm_provider_id` | `""` | 固定小黑盒 LLM Provider；留空跟随当前配置或会话 |
| `providers.image_provider_id` | `""` | 固定图片理解 Provider；留空使用 AstrBot 原生图片流程 |
| `context.thread_reply_post_chars` | `1600` | 评论回复/@ 时原帖低优先级正文预算；主动刷帖不使用 |
| `context.thread_reply_recent_comments` | `12` | 评论回复/@ 时最近楼层窗口；直接回复对象另行保留 |
| `reply.dry_run_mark_processed` | `true` | 模拟结果保存后标记完成 |
| `reply.max_reply_chars` | `500` | 回复字符上限 |
| `reply.only_explicit_mentions` | `true` | 只处理明确 @ |
| `network.max_reply_concurrency` | `2` | 回复 worker 数 |
| `network.max_pending_events` | `50` | 总待处理上限 |
| `network.max_pending_per_user` | `5` | 单用户待处理上限 |
| `proactive_feed.enabled` | `false` | 主动刷帖 |
| `proactive_feed.dry_run` | `true` | 主动回复仅生成、不真实发送 |
| `proactive_feed.review_required` | `true` | 真实发送前要求人工审核；关闭后可直接发送 |

完整字段和边界见管理页及 [_conf_schema.json](_conf_schema.json)。

## 会话与上下文

小黑盒评论区按“帖子 + 根评论楼层”隔离：

```text
group_id          xhh_post_<post_id>
帖子 session_id   xhh_post_<post_id>
楼层 session_id   xhh_thread_<post_id>_<root_comment_id>
message_id        xhh_<event_type>_<notification_id>_<comment_id>
```

同一根楼层始终使用同一个 session；不同帖子和不同根楼层各自独立。帖子标题、正文、作者、
父/根评论、楼层和必要图片通过 `on_llm_request` 注入：

```xml
<xiaoheihe_context trust="untrusted">
  ...
</xiaoheihe_context>
```

这些内容使用 `TextPart.mark_as_temp()` 作为本轮用户侧临时上下文，当前评论正文作为真正的
用户消息进入 AstrBot 会话。

评论回复/@ 会进一步按“当前原生用户消息 > 直接回复对象 > 最近楼层 > 原帖背景”建立焦点：
原帖低优先级正文默认限制为 1600 字，普通楼层窗口默认只取最近 12 条且单条最多 800 字；
通知中的直接回复对象单独保留，并从普通楼层窗口去重。背景末尾仅临时重复一份当前消息用于
定位，再追加可信焦点规则；当前消息能独立理解时不得为了迎合原帖强行关联。主动刷帖不使用
这些楼层回复预算，仍按 `max_post_chars` / `max_thread_comments` 读取原帖和辅助评论。

## 图片理解

公开 HTTPS 图片 URL 会转换为 AstrBot 原生 `Image` 组件。默认每个事件最多 6 张图片。插件
会检查协议、认证信息、主机和 DNS 结果，过滤本地地址、内网地址与保留地址。配置固定图片
Provider 后，插件先让该 Provider 生成本轮临时图片描述，再交给固定或当前 LLM Provider；图片
Provider 不存在、调用失败或返回空描述时会保留原图并回退 AstrBot 原生图片流程。未固定图片
Provider 且当前 LLM Provider 明确声明为纯文本能力时，本轮按纯文本继续处理，并在管理页显示
提示。

v1.2.2 的图片能力范围是接收和理解；评论图片上传列为待真实账号验证项。

## 身份与回复规则

处理优先级：

```text
机器人自身 → 黑名单 → 主人 UID → 白名单 → 普通触发规则
```

- UID 全部按字符串处理；
- 主人 UID 可绕过普通白名单并获得较高队列优先级；
- `map_owner_to_astrbot_admin` 是独立开关，默认关闭；
- 登录 UID、评论 ID、本地发送记录、内容哈希、路由和 SQLite 唯一约束共同防止自循环；
- 同一事件聚合全部流式文本后提交一条评论；
- 评论接口明确失败时记录终态；
- 超时或连接中断时进入 `send_unknown`，交由近期评论核对和人工检查。

## 主动刷帖

默认设置：

```yaml
enabled: false
dry_run: true
review_required: true
interval_seconds: 900
max_per_run: 1
max_per_day: 10
source: all
```

启用后，合格帖子通过 AstrBot 原生事件链路生成主动回复。`dry_run` 和 `review_required` 是
相互独立的开关：

| `dry_run` | `review_required` | 行为 |
| --- | --- | --- |
| `true` | `false` | 自动生成并记录模拟结果，不发送、不进入人工审核 |
| `true` | `true` | 保存到“主动审核”；批准只确认模拟结果，不真实发送 |
| `false` | `true` | 保存到“主动审核”；批准后发送已审核文本，不再次调用模型 |
| `false` | `false` | AI 生成后直接真实发表评论，不等待人工审核 |

最后一种属于高风险模式。插件启动时会记录警告，但不会再将其视为非法配置或阻止插件加载；
真实发送仍复用普通回复的楼层锁、发送记录、重复发送闸门和发送状态未知核对。建议先保持
`dry_run: true` 验证筛选规则、人格和回复质量，再逐步降低 `max_per_run`、`max_per_day` 后开启
直发。

`max_per_day` 表示每天最多把多少个合格帖子提交给 AI 生成主动回复；读取推荐流、本地过滤、
黑名单和去重均不消耗额度。达到上限后适配器不再提交新的主动生成事件。推荐流分区始终使用
小黑盒已验证的 `pull=0` 请求，管理页的中文分区仅用于筛选返回帖子的主题和标签。

## 数据与安全

```text
data/plugin_data/astrbot_plugin_xiaoheihe_adapter/
├── credentials/<profile_id>.json
├── xiaoheihe.db
├── logs/
└── cache/
```

- 凭证使用临时文件和原子替换保存；
- POSIX 环境尽量设置目录 `0700`、文件 `0600`；
- Windows 建议使用 AstrBot 运行账号 ACL 保护数据目录；
- Cookie、Token、设备 ID 和敏感响应经过日志脱敏；
- SQLite 使用 WAL、参数化 SQL、唯一索引和迁移；
- v1.2.12 数据库迁移版本仍为 **v6**；
- 自动清理启动后延迟执行，之后每 24 小时执行一次；
- 清理范围限定在插件自己的数据库、日志和缓存。

### 更新与数据继承

AstrBot 的插件更新替换 `data/plugins` 下的代码。小黑盒运行数据使用独立目录
`data/plugin_data/astrbot_plugin_xiaoheihe_adapter/`，普通覆盖更新会继续读取原有凭证、
SQLite、去重记录、通知游标、审核候选和日志；插件配置继续由原有 `AstrBotConfig` 提供。

更新前建议备份整个插件数据目录。卸载时选择删除插件数据、手动删除该目录或更改插件内部
名称会改变继承结果。

主要表：`account_state`、`incoming_events`、`processed_event_keys`、`outgoing_replies`、
`self_comment_ids`、`session_mappings`、`feed_candidates`、`runtime_errors`、
`daily_counters`、`notification_cursors`。

## 常见排障

| 现象 | 检查方法 |
| --- | --- |
| 新增适配器里找不到“小黑盒” | 确认插件已启用并完成重载，检查 `main.py` 导入和平台注册日志 |
| 覆盖更新后适配器实例为空 | 升级 v1.2.2；插件会在热重载阶段重建已启用实例，日志显示“插件更新后已重新加载小黑盒适配器实例” |
| 扫码后停在等待状态 | 在手机端确认登录，回到管理页点击“检查登录”；二维码过期时重新生成 |
| 提示 `relogin` / 401 | 在管理页安全退出并重新扫码，确认状态恢复为 `success` |
| 连续 403 | 保持模拟运行，等待熔断结束，并核对脱敏诊断与接口契约 |
| 持续 429 | 增大轮询和请求间隔，减少分页与并发，按 `Retry-After` 等待 |
| 历史 @ 进入事件记录 | 升级 v1.2.2，等待“通知历史基线已建立”后再发送新 @ |
| 同一消息出现多条回复 | 升级 v1.2.2，检查事件状态和 `outgoing_replies`；`send_unknown` 交由人工核对 |
| 实际评论只显示工具状态、第一段或“正在查询” | 升级 v1.2.9；适配器会等待 Agent 完成，并兼容 AstrBot 分段和流式回复后一次性提交最终内容 |
| 带图问题调用 Grok 后只返回图片描述或“等着我查” | 升级 v1.2.10；普通 `grok_web_search` 查询会在工具执行期间临时隔离事件原图，明确搜图时仍保留原图 |
| 同一楼层不同用户接话时“我/我的”被当成上一位用户 | 升级 v1.2.11；楼层继续共享会话，但每轮用户历史都会持久化真实发送者 UID，并在当前轮注入可信身份约束 |
| 评论区已经歪楼，机器人却仍围着原帖答非所问 | 升级 v1.2.12；被动楼层回复会优先当前消息和直接回复对象，并限制原帖/普通楼层背景预算；主动刷帖仍以原帖为主 |
| 多图总结在 120 秒进入 `dead_letter` | 升级 v1.2.2；基础超时保持原配置，适配器按图片数量自动增加视觉处理时间，6 图默认截止时间为 300 秒 |
| @ 数量增加但事件为空 | 检查 `message_type`、接收条数、权限过滤和基线日志 |
| `status=failed / code 1000` | 该发送尝试记录为失败终态；结合 API 契约核对评论请求字段 |
| 图片按纯文本处理 | 检查模型视觉能力、图片 HTTPS 地址和 SSRF 校验提示 |
| 关闭主动审核后插件加载失败 | 升级 v1.2.8；无审核真实发送已成为受支持的显式模式 |
| 后台任务退出 | 查看运行日志中的任务名和首个异常，修复后重载插件 |

管理页“复制脱敏诊断”在浏览器权限受限时会显示只读文本框，可直接手动复制。

## 开发与测试

```bash
python -m pip install -e ".[test]"
ruff check .
ruff format --check .
python -m coverage run -m pytest -q
python -m coverage report
python -m compileall -q .
python tools/validate_repository.py
```

普通测试使用 Mock HTTP 和脱敏 fixture。真实集成测试需要显式环境变量，并建议保持模拟运行。
测试范围与实际执行结果见 [测试说明](docs/testing.md)。

仓库已配置 CI、CodeQL、Dependency Review、Gitleaks 和 Dependabot。建议为 `main` 开启分支
保护：PR 通过 CI、CodeQL 无高危结果、至少一次审查，并关闭 force push。

## 相关文档

- [架构说明](docs/architecture.md)
- [AstrBot 兼容性](docs/compatibility.md)
- [小黑盒 API 契约](docs/xiaoheihe-api-contract.md)
- [测试说明](docs/testing.md)
- [安全策略](SECURITY.md)
- [贡献指南](CONTRIBUTING.md)

## 致谢

- [AstrBot](https://github.com/AstrBotDevs/AstrBot)：平台适配器与原生 Agent 管线；
- [SomeOvO/xhhRobot](https://github.com/SomeOvO/xhhRobot)：登录、通知、帖子与评论功能行为参考；
- [XiaHouSheng/heybox-core](https://github.com/XiaHouSheng/heybox-core)：MIT 许可的动态 `hkey`
  行为参考；
- [HadeonYu/heybox-bot](https://github.com/HadeonYu/heybox-bot)：MIT 许可的 Web 登录参数和客户端
  身份形状参考；
- [674537331/astrbot_plugin_mihome](https://github.com/674537331/astrbot_plugin_mihome)：
  README 排版与中文信息组织参考。

本项目采用 Python 独立实现。第三方许可全文见
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## License

[MIT License](LICENSE) © RyanVaderAn
