# AstrBot 小黑盒适配器

> 将小黑盒 @、评论回复、帖子上下文与主动浏览接入 AstrBot 原生会话、人格和 Agent 链路。

[![CI](https://github.com/674537331/astrbot_plugin_xiaoheihe_adapter/actions/workflows/ci.yml/badge.svg)](https://github.com/674537331/astrbot_plugin_xiaoheihe_adapter/actions/workflows/ci.yml)
[![CodeQL](https://github.com/674537331/astrbot_plugin_xiaoheihe_adapter/actions/workflows/codeql.yml/badge.svg)](https://github.com/674537331/astrbot_plugin_xiaoheihe_adapter/actions/workflows/codeql.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

当前版本：**v1.3.3**

小黑盒通知会转换为 `AstrBotMessage`，通过 `commit_event()` 进入 AstrBot 原生事件队列。模型、人格、会话历史、记忆、Agent、MCP、Skills、Web Search 和已授权工具仍由 AstrBot 负责；本插件负责小黑盒平台接入、上下文构建、图片预处理、幂等发送与主动浏览。

## v1.3.3 重点

- 楼层上下文按局部相关性路由：普通短回复不新增额外 LLM 判断；长帖的短楼层也不会仅因原帖很长触发辅助压缩。只有既有长楼层压缩明确判定 `drifted` 时才省略低相关原帖文字与视觉，明确引用原帖时立即恢复。
- 图片来源扩展为“当前评论 → 直接回复对象 → 楼层锚点 → 原帖”；近距离图片优先占用视觉槽位，楼层树漏掉被回复评论图片时可使用同一通知里的直接回复对象媒体回退，并继续执行所有者/来源校验。
- 长楼层压缩拥有独立超时预算，同一事件最多真正尝试一次压缩 Provider；相关性结果只用于内部路由，最终回复焦点只要求围绕当前消息和局部回复链作答，不注入“偏离/歪楼”标签，也不要求模型评价当前话题与原帖的相关性。失败后直接使用确定性上下文 fallback，为慢 Provider 主回复和 fallback 保留预算。无新增小黑盒 API、数据库迁移、配置项或运行依赖。

## v1.3.2 重点

- 收敛昵称、UID 与本地身份锚点在模型上下文中的重复展开：当前发送者完整身份继续由可信 `xiaoheihe_sender_identity` 持久绑定，临时 runtime/community 上下文只引用该绑定；身份仅用于发言归属和第一人称消歧，非必要时明确要求回复正文不要主动称呼、复述或评价身份值。
- 长楼层压缩继续使用本地 `speaker_n` 校验身份，但同一参与者的完整身份在相关背景中最多展开一次；直接回复对象保留原文与必要归属，已经在直接回复对象中绑定的身份不会在参与者锚点再次展开。图片压缩器同样禁止把图片所有者昵称、UID、角色或本地锚点写进视觉摘要。
- 修正真实分区时代码仍把主动事件描述为“主动浏览推荐流”的旧上下文文案，统一为来源无关的主动浏览表述；不新增小黑盒 API、数据库迁移或运行依赖，权限、审核、额度、发送与真实分区抓取语义保持不变。

完整历史见 [CHANGELOG.md](CHANGELOG.md)。

## 核心特性

- 原生平台类型 `xiaoheihe`，可在 AstrBot “机器人 → 新增适配器”中直接添加；
- Plugin Page 扫码登录、状态总览、设置、浏览来源、事件记录、主动审核、运行日志和存储管理；
- @ 与直接回复进入 AstrBot 原生 Agent，不旁路人格、会话或工具链；
- 同一帖子/楼层共享公开讨论上下文，同时为每轮发送者保留昵称 + UID / 不可授权的本地备用身份锚点；身份值用于归属和消歧，不作为默认称呼提示；
- 长楼层可按来源语义压缩，当前消息与直接回复对象始终保留原文；
- 图片在主 Agent 构建前按“插件识图 Provider → AstrBot 默认图片转述 Provider → AstrBot 当前主模型”处理，全部失败时隔离原图并禁止最终模型猜图；
- 主动帖子视觉描述可保存为 24 小时文字快照，不保存图片字节；
- Agent 工具状态、分段回复和流式片段统一聚合，最终只提交一条小黑盒评论；
- SQLite 负责通知边界、幂等、重试、候选审核、发送闸门、视觉快照和清理；
- 结构化日志、诊断导出与 Plugin Page 返回均进行脱敏。

## 环境要求

- AstrBot `>=4.24.2,<5`；
- Python 3.12–3.14；
- 可访问小黑盒所需 HTTPS 域名。

CI 会持续验证最低支持版本 4.24.2、重点版本 4.26.2 和当前支持范围内的最新稳定 AstrBot。详细结论见 [兼容性说明](docs/compatibility.md)。

## 安装

在 AstrBot 插件市场或插件安装页使用仓库地址：

```text
https://github.com/674537331/astrbot_plugin_xiaoheihe_adapter
```

手动部署时，将仓库放入 AstrBot 插件目录并安装依赖：

```bash
python -m pip install -r requirements.txt
```

## 快速上手

```text
安装并启用插件
  → 打开插件详情页“小黑盒管理”
  → 扫码登录并确认账号状态 success
  → 在“机器人 → 新增适配器”添加“小黑盒”并绑定同一 profile_id
  → 保持模拟运行，等待 mention / reply 通知历史基线建立
  → 用另一个账号发送一条新的 @ 或回复
  → 在事件记录核对上下文与生成结果
  → 再按需要配置“浏览来源”和主动回复策略
```

### 扫码登录

1. 打开“插件 → 小黑盒适配器 → 小黑盒管理”；
2. 进入“扫码登录”，选择账号档案；
3. 点击“生成二维码”，使用小黑盒客户端扫码并确认；
4. 点击“检查登录”，直到状态为 `success`；
5. 核对昵称、UID、登录时间和最近检查时间。

凭证保存在插件数据目录，不写入普通插件配置，也不会在诊断中导出完整 Cookie/Token。

### 创建适配器

进入“机器人 → 新增适配器”，选择“小黑盒”：

| 字段 | 用途 |
| --- | --- |
| `id` | AstrBot 平台实例 ID |
| `enable` | 是否启用实例 |
| `profile_id` | 绑定“小黑盒管理”中的账号档案 |

### 建立通知基线

首次成功轮询会分别记录 `mention` 和 `reply` 的当前最新 `message_id`。默认 `initial_backfill_count: 0`，已有消息中心内容作为历史基线，之后的新通知才进入正常处理。

建议先在日志看到：

```text
mention 通知历史基线已建立
reply 通知历史基线已建立
```

再用其他账号发送新的 @ / 回复进行验证。

## 主动浏览来源

v1.3.0 将浏览来源独立为“小黑盒管理 → **浏览来源**”。

### 真实分区浏览（推荐）

页面会只读探测小黑盒当前返回的真实分区目录，并显示例如：

```text
真实分区浏览（推荐）
已选择：无畏契约、Gal游戏综合区、动漫
3 个分区
[探测/刷新真实分区] [修改选择]
```

行为：

- 分区探测仅执行 GET，不调用 AI、不发帖、不评论、不修改小黑盒账号；
- 最多选择 20 个真实分区；
- 可搜索分区名称、所属分组或 `topic_id`；
- 多分区并发读取后按帖子 ID 去重；
- 单个分区失败不会影响其他分区；
- 只有 **全部所选真实分区均失败** 时才进入推荐流回退；
- 任一真实分区成功时，不把推荐流帖子混入这一轮。

小黑盒这些接口不是公开稳定 API，因此目录或分区流字段未来可能变化。探测失败不会修改账号；具体契约和验证边界见 [小黑盒 API 契约](docs/xiaoheihe-api-contract.md)。

### 兼容/回退推荐流分类

旧“推荐流分区”已改成多选的 **兼容/回退推荐流分类**。

- 未选择任何真实分区：推荐流分类直接生效；
- 已选择真实分区：界面置灰并提示“已启用真实分区浏览，此项当前不生效”；
- 本轮真实分区全部读取失败：自动使用已保存的回退分类；
- “全部”与其他分类互斥；
- 推荐流分类仍是对 `/bbs/app/feeds` 返回结果的本地主题/标签过滤，不等同于服务端真实分区。

可选分类包括：PC 游戏、手机游戏、主机游戏、盒友杂谈、盒友日常、数码科技、动漫二次元、影视娱乐、电竞赛事、游戏攻略、优惠资讯、独立游戏等。

## 主动回复安全模式

主动浏览与“是否发表评论”是两层独立逻辑：浏览来源只决定从哪里找帖子，发送策略仍由以下设置决定。

| `proactive_feed.dry_run` | `review_required` | 行为 |
| --- | --- | --- |
| `true` | 任意 | 只生成/记录，不真实发送 |
| `false` | `true` | 生成候选，人工批准后真实发送 |
| `false` | `false` | AI 生成后直接真实发送，高风险显式模式 |

默认：主动刷帖关闭、`dry_run=true`、`review_required=true`。

## 常用设置

通用参数在“小黑盒管理 → 设置”维护；主动来源在“浏览来源”维护。两者最终保存到同一个 `AstrBotConfig`。

| 设置 | 默认值 | 说明 |
| --- | --- | --- |
| `polling.poll_interval_seconds` | `60` | 通知轮询间隔，最低 30 秒 |
| `polling.max_pages_per_poll` | `3` | 每类通知单轮最大页数；超出后跨轮回填 |
| `polling.initial_backfill_count` | `0` | 首次基线后回溯条数 |
| `providers.llm_provider_id` | `""` | 小黑盒主 Agent 首选 Provider |
| `providers.image_provider_id` | `""` | 首选识图 Provider |
| `providers.context_provider_id` | `""` | 长楼层语义压缩 Provider |
| `context.enable_thread_reply_compression` | `true` | 长被动楼层来源感知语义压缩 |
| `context.max_images_per_event` | `6` | 每事件最多处理图片数 |
| `context.image_total_timeout_seconds` | `240` | 单事件视觉链总预算 |
| `reply.max_reply_chars` | `500` | 最终回复字符上限 |
| `network.max_reply_concurrency` | `2` | 回复 worker 数 |
| `network.max_pending_events` | `50` | 总待处理上限 |
| `proactive_feed.enabled` | `false` | 是否启用主动刷帖 |
| `proactive_feed.dry_run` | `true` | 主动回复只生成、不发送 |
| `proactive_feed.review_required` | `true` | 真实发送前是否人工审核 |
| `proactive_feed.topic_ids` | `[]` | 真实分区 ID；由“浏览来源”页管理，最多 20 个 |
| `proactive_feed.fallback_sources` | `["all"]` | 兼容/回退推荐流分类；由“浏览来源”页管理，可多选 |
| `proactive_feed.max_per_run` | `1` | 每轮主动 AI 请求上限 |
| `proactive_feed.max_per_day` | `10` | 每日主动 AI 请求上限；本地浏览/过滤不计数 |

旧版 `proactive_feed.source` 只保留用于升级迁移，不再作为当前 UI 设置项。

## 会话、上下文与身份

确定性路由：

```text
group_id          xhh_post_<post_id>
帖子 session_id   xhh_post_<post_id>
楼层 session_id   xhh_thread_<post_id>_<root_comment_id>
message_id        xhh_<event_type>_<notification_id>_<comment_id>
```

同一根楼层共享一个公开讨论 session。当前发言人、原帖作者、楼层参与者、直接回复对象以及图片所有者由本地结构化数据区分；正常情况使用真实 UID，API 缺失 UID 时使用不可获得主人/管理员/白名单权限的本地备用锚点。

v1.3.2 起，当前发送者完整身份只由可信 `xiaoheihe_sender_identity` 绑定负责跨轮历史归属；临时运行时/社区背景不再重复展开当前昵称和 UID。原帖作者、楼层参与者与图片所有者仍在必要来源处绑定，但同一信息不为了“提醒模型”而跨字段反复复制；直接回复对象作为高相关性原文例外保留必要归属。模型被明确要求只把这些值用于内容归属和第一人称消歧，而不是默认在回复正文中称呼用户。

被动评论/@ 按以下焦点组织临时背景：

```text
当前用户消息 > 直接回复对象 > 当前楼层锚点 / 最近楼层 > 原帖背景
```

普通短回复继续使用有界确定性窗口，不为了判断是否歪楼额外请求模型。长楼层达到既有压缩阈值后，插件复用同一次上下文压缩结果：`related` / `partial` / `unclear` 保留原帖；只有明确 `drifted` 才在当前轮省略低相关原帖文字和原帖视觉。用户重新明确提到原帖、楼主、帖子内容或原帖图片时立即恢复原帖背景。压缩异常、超时或格式错误会回退确定性窗口，不阻断本轮回复。

## 图片理解

插件只接收通过安全检查的公开 HTTPS 图片 URL，不保存原始图片字节。小黑盒图片按当前轮对话距离排序：

```text
当前评论图片
  → 直接回复对象图片
  → 楼层锚点图片
  → 原帖图片
```

随后在 AstrBot 主 Agent 构建前依次尝试：

```text
插件 image_provider_id
  → AstrBot 默认图片转述 Provider
  → AstrBot 当前主模型
```

近距离回复链图片优先占用有限图片槽位；因此在他人图片楼层中 @ Bot 时，被直接回复的图片不会被主贴图片挤掉。全部候选失败时原图会从最终请求中隔离，并加入明确的不可见失败说明，要求模型承认无法读取图片而不是猜测；当前评论与直接回复对象这两个最近视觉来源保留受控的 AstrBot 原生视觉最后兜底。普通 `grok_web_search` 查询也不会重复吃到已经处理的原图；只有明确的搜图/识图意图才在工具调用期间临时开放受限图片引用。

## 风险与接口稳定性

小黑盒相关接口属于可能变化的客户端接口，不是公开稳定自动化 API。项目根据公开可研究行为和许可清晰的参考实现独立实现 Python 客户端，并使用脱敏 fixture / Mock HTTP 做自动测试。

建议：

- 新账号或大版本升级后先保持 dry-run；
- 真实发送前在事件记录核对回复目标、上下文和生成文本；
- 遇到 `relogin` / 401 重新扫码；
- 遇到持续 429 增大轮询间隔并减少并发；
- `send_unknown` 不自动重复 POST，先人工核对小黑盒实际评论状态；
- 不公开粘贴 Cookie、Token、设备 ID、二维码、数据库或包含私人正文的日志。

## 常见问题

| 现象 | 检查方法 |
| --- | --- |
| 新增适配器里找不到“小黑盒” | 确认插件已启用并完成重载，检查平台注册日志 |
| 扫码后仍等待 | 手机端确认后回管理页执行“检查登录”；过期则重新生成二维码 |
| `relogin` / 401 | 安全退出并重新扫码 |
| 持续 403 / 429 | 查看脱敏日志；等待冷却或增大请求间隔 |
| 历史 @ 被处理 | 确认通知历史基线已建立后再发送新的测试通知 |
| 同一消息疑似重复发送 | 查看事件与 `outgoing_replies` 状态；`send_unknown` 先人工核对 |
| 工具状态/第一段被发成评论 | v1.2.9+ 会等待 Agent 最终完成并聚合分段；检查当前插件版本和日志 |
| 图片模型先后顺序不对 | v1.2.16+ 应为插件识图 → AstrBot 识图 → AstrBot 主模型 |
| 找不到想要的真实分区 | 在“浏览来源”点击“探测/刷新真实分区”，按名称/分组/topic_id 搜索 |
| 真实分区全部失败 | 查看探测错误和日志；本轮应自动使用已保存的回退推荐流分类 |
| 已选真实分区却仍看到推荐流 | 只有全部真实分区请求失败时才应回退；事件候选原因会标记“真实分区 / 回退推荐流”来源 |

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

普通测试使用 Mock HTTP 和脱敏 fixture。仓库配置 CI、CodeQL、Dependency Review、Secret Scan 和 Dependabot。测试范围与最近一次验证结果见 [测试说明](docs/testing.md)。

## 相关文档

- [更新日志](CHANGELOG.md)
- [架构说明](docs/architecture.md)
- [AstrBot 兼容性](docs/compatibility.md)
- [小黑盒 API 契约](docs/xiaoheihe-api-contract.md)
- [测试说明](docs/testing.md)
- [安全策略](SECURITY.md)
- [贡献指南](CONTRIBUTING.md)

## 致谢

- [AstrBot](https://github.com/AstrBotDevs/AstrBot)：平台适配器与原生 Agent 管线；
- [SomeOvO/xhhRobot](https://github.com/SomeOvO/xhhRobot)：登录、通知、帖子与评论功能行为研究；
- [XiaHouSheng/heybox-core](https://github.com/XiaHouSheng/heybox-core)：MIT 许可的动态 `hkey` 行为参考；
- [HadeonYu/heybox-bot](https://github.com/HadeonYu/heybox-bot)：MIT 许可的 Web 登录参数和客户端身份形状参考。

本项目采用 Python 独立实现。第三方许可全文见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## License

[MIT License](LICENSE) © RyanVaderAn
