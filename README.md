# AstrBot 小黑盒适配器

> 将小黑盒 @、评论回复和帖子上下文接入 AstrBot 原生会话、人格与 Agent 链路。

[![CI](https://github.com/674537331/astrbot_plugin_xiaoheihe_adapter/actions/workflows/ci.yml/badge.svg)](https://github.com/674537331/astrbot_plugin_xiaoheihe_adapter/actions/workflows/ci.yml)
[![CodeQL](https://github.com/674537331/astrbot_plugin_xiaoheihe_adapter/actions/workflows/codeql.yml/badge.svg)](https://github.com/674537331/astrbot_plugin_xiaoheihe_adapter/actions/workflows/codeql.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

当前版本：**v1.1.3**

小黑盒通知会转换为 `AstrBotMessage`，通过 `commit_event()` 进入 AstrBot 原生事件队列。回复
继续使用当前 AstrBot 模型、人格、会话历史、记忆、Agent、MCP、Skills、Web Search 和已授权
工具。插件只负责平台接入，不单独配置模型接口。

v1.1.3 重点：

- 多图事件按实际图片数量自动增加视觉处理时间，默认 6 图事件由 120 秒扩展为 300 秒；
- 设置保存后显示成功提示，并注明配置是否产生变化；
- 评论区 @ 会同时读取当前评论与原帖的文本、图片，再合并指定楼层；
- AstrBot 分段回复会在清理前保存完整文本，任意段数均一次性提交小黑盒评论；
- 覆盖更新或插件热重载后自动重建已启用的小黑盒适配器实例；
- 平台适配器使用仓库 `logo.png` 展示图标；
- 首次轮询按小黑盒 `message_id` 建立持久化历史基线；
- 新通知先写入 SQLite，再进入有界处理队列，拥塞事件按到期时间恢复；
- 同一入站事件使用数据库发送记录和事件级闸门限制为一次评论提交；
- 主动审核使用原子发送状态和账号级并发闸门；
- 覆盖更新保留账号凭证、SQLite、配置和通知游标。

## 核心特性

- 原生平台类型 `xiaoheihe`；
- Plugin Page 扫码登录和中文管理页；
- @ 与直接回复进入 AstrBot 原生消息管线；
- 当前评论、原帖、根楼层、引用、作者和双方图片作为本轮临时上下文；
- “帖子 + 根楼层”确定性会话隔离；
- SQLite 幂等、状态机、迁移、恢复和自动清理；
- 模拟运行完成完整推理并保存结果；
- 白名单、黑名单、主人 UID 和自身消息过滤；
- 主动刷帖默认关闭，启用后默认模拟运行并进入人工审核；
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

然后使用另一个账号发布一条全新的 @。升级 v1.1.3 后也建议先等待该日志，再开始验证。

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

管理页按账号、轮询、上下文与图片、回复策略、身份过滤、网络并发、主动帖子、数据保留和
日志分组展示。保存时后端校验并调用 `AstrBotConfig.save_config()`，受影响的后台任务会安全
刷新。

| 设置 | 默认值 | 说明 |
| --- | --- | --- |
| `profiles[].dry_run` | `true` | 模拟运行 |
| `polling.poll_interval_seconds` | `60` | 通知轮询间隔，最低 30 秒 |
| `polling.max_pages_per_poll` | `3` | 每类通知每轮最大页数 |
| `polling.initial_backfill_count` | `0` | 首次基线后回溯条数 |
| `reply.dry_run_mark_processed` | `true` | 模拟结果保存后标记完成 |
| `reply.max_reply_chars` | `500` | 回复字符上限 |
| `reply.only_explicit_mentions` | `true` | 只处理明确 @ |
| `network.max_reply_concurrency` | `2` | 回复 worker 数 |
| `network.max_pending_events` | `50` | 总待处理上限 |
| `network.max_pending_per_user` | `5` | 单用户待处理上限 |
| `proactive_feed.enabled` | `false` | 主动刷帖 |
| `proactive_feed.review_required` | `true` | 主动回复人工审核 |

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

## 图片理解

公开 HTTPS 图片 URL 会转换为 AstrBot 原生 `Image` 组件，由核心媒体流程处理。默认每个事件
最多 6 张图片。插件会检查协议、认证信息、主机和 DNS 结果，过滤本地地址、内网地址与保留
地址。当前提供商声明为纯文本能力时，本轮按纯文本继续处理，并在管理页显示提示。

v1.1.3 的图片能力范围是接收和理解；评论图片上传列为待真实账号验证项。

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
```

启用后，候选内容通过 AstrBot 原生事件链路生成并保存到“主动审核”。审核页支持编辑、批准和
拒绝；批准后发送已审核文本，无需再次调用模型。

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
- v1.1.3 数据库迁移版本为 **v4**；
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
| 覆盖更新后适配器实例为空 | 升级 v1.1.3；插件会在热重载阶段重建已启用实例，日志显示“插件更新后已重新加载小黑盒适配器实例” |
| 扫码后停在等待状态 | 在手机端确认登录，回到管理页点击“检查登录”；二维码过期时重新生成 |
| 提示 `relogin` / 401 | 在管理页安全退出并重新扫码，确认状态恢复为 `success` |
| 连续 403 | 保持模拟运行，等待熔断结束，并核对脱敏诊断与接口契约 |
| 持续 429 | 增大轮询和请求间隔，减少分页与并发，按 `Retry-After` 等待 |
| 历史 @ 进入事件记录 | 升级 v1.1.3，等待“通知历史基线已建立”后再发送新 @ |
| 同一消息出现多条回复 | 升级 v1.1.3，检查事件状态和 `outgoing_replies`；`send_unknown` 交由人工核对 |
| 实际评论只显示模型回复前几段 | 升级 v1.1.3；适配器会恢复分段前的完整文本。管理页出现分段回复提醒时，可在 AstrBot 平台设置关闭分段回复以减少等待 |
| 多图总结在 120 秒进入 `dead_letter` | 升级 v1.1.3；基础超时保持原配置，适配器按图片数量自动增加视觉处理时间，6 图默认截止时间为 300 秒 |
| @ 数量增加但事件为空 | 检查 `message_type`、接收条数、权限过滤和基线日志 |
| `status=failed / code 1000` | 该发送尝试记录为失败终态；结合 API 契约核对评论请求字段 |
| 图片按纯文本处理 | 检查模型视觉能力、图片 HTTPS 地址和 SSRF 校验提示 |
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
