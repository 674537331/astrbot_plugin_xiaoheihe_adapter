# 小黑盒 API 契约与验证状态（v1.3.0）

## 重要声明

小黑盒相关接口属于非公开、可能变化的客户端契约，不是平台承诺稳定的自动化 API。本项目根据公开可研究行为、许可清晰的参考实现和真实运行反馈进行独立 Python 实现，并将不稳定路径集中在 `endpoints.py` / `parsers.py` / `topic_service.py` 中。

自动测试默认使用脱敏 fixture 与 `httpx.MockTransport`，不会访问真实账号。真实环境行为与自动化契约测试必须区分：测试通过表示本项目能处理当前已知响应形状，不等于小黑盒未来不会改变接口。

## 当前端点

| 用途 | 方法 | 路径 | 认证 | 备注 |
| --- | --- | --- | --- | --- |
| 获取二维码 | GET | `/account/get_qrcode_url/` | 否 | 匿名会话 |
| 查询二维码状态 | GET | `/account/qr_state/` | 否 | 与二维码请求复用匿名 Client |
| 当前账号检查 | GET | `/bbs/app/api/user/permission` | 是 | 认证失效会触发 relogin/熔断处理 |
| 用户消息中心 | GET | `/bbs/app/user/message` | 是 | mention/reply 轮询 |
| 帖子/楼层树 | GET | `/bbs/app/link/tree` | 是 | 原帖与评论上下文 |
| 创建评论 | POST | `/bbs/app/comment/create` | 是 | `workshopapi.xiaoheihe.cn` |
| 近期机器人评论 | GET | `/bbs/app/comment/user` | 是 | 用于发送状态未知核对 |
| 推荐信息流 | GET | `/bbs/app/feeds` | 是 | v1.3.0 兼容/回退流 |
| 真实分区目录 | GET | `/bbs/app/api/topic/index/` | 是 | v1.3.0 只读探测 |
| 真实分区帖子流 | GET | `/bbs/app/topic/feeds` | 是 | v1.3.0 主动浏览首选 |

端点定义以 `xiaoheihe/endpoints.py` 为唯一代码来源。

## Web 客户端与签名

认证请求由 `XiaoheiheApiClient` 补充 Web 客户端环境、稳定设备身份和动态签名。当前 Web 环境包含：

```text
os_type=web
client_type=web
version=999.0.4
web_version=2.5
x_client_type=web
x_app=heybox_website
x_os_type=Windows
device_info=Chrome
```

每次需要签名的请求生成动态时间、nonce 和 `hkey`；Cookie、Token、二维码参数、设备 ID 和完整签名值不会写入普通日志或诊断导出。

## 通知契约

### @ 通知

当前请求形状：

```text
GET /bbs/app/user/message
message_type=16/17
 offset=<n>
 limit=<n>
 no_more=false
```

真实环境已观察到评论 @ 使用 `message_type=17`。解析器兼容 `result.messages` 和已知顶层结果形状。

### 评论/回复

当前请求使用 `list_type=0` 分页，并保留已知回复消息类型。通知规范化关注：

```text
message_id
comment_a_id
comment_a_text
root_comment_id
linkid
userid_a
user_a
```

通知游标按 `profile_id + notification_type` 持久化最新 `message_id`。首次默认只建立历史基线；超过单轮页数的旧区间持久化为 backfill，实时 cursor 可以继续推进。

## 推荐流契约

v1.3.0 的兼容/回退推荐流仍调用：

```text
GET /bbs/app/feeds
app=heybox
pull=0
offset=0
```

**不会**把 `pc_game`、`anime` 等本地分类名称作为 `source` 参数发送给小黑盒。

推荐流分类来自解析后的 `section_names` / topics / hashtags / content tags，再由本地 `SECTION_ALIASES` 匹配。因此：

- 它不是服务端真实分区选择；
- 分类准确率取决于推荐流本轮返回内容和标签；
- 只在未配置真实分区或全部真实分区本轮请求失败时使用。

## 真实分区目录契约

v1.3.0 `fetch_topic_catalog()` 调用：

```text
GET /bbs/app/api/topic/index/
type=list
```

解析器从 `result` / `data` / 顶层对象中寻找 `topics_list`，递归扁平化后保留：

```json
{
  "id": "<numeric topic_id>",
  "name": "<topic name>",
  "group": "<parent group>"
}
```

`topic_id` 必须是 1–32 位数字；当前最多允许用户选择 20 个。

如果目录为空或已知字段全部不存在，插件抛出 `response_shape` 错误，不猜测分区 ID。

## 真实分区帖子流契约

`fetch_topic_feed()` 调用：

```text
GET /bbs/app/topic/feeds
topic_id=<1-32 digit id>
offset=<>=0>
limit=<1..30>
lastval=<bounded string>
dw=720
```

默认首屏 `limit=10`。返回内容继续复用通用 `parse_feed()`，并为每条帖子加：

```text
source_topic_id=<requested topic_id>
```

### 多分区运行时语义

- 最多 4 路并发；
- 相同帖子跨分区出现时按帖子 ID 去重；
- 合并后按 `created_at`、`popularity_score` 倒序；
- 单个分区异常被隔离；
- `CancelledError` 不作为分区失败处理；
- 所有分区都失败时才拉一次推荐流回退；
- 任一分区成功时不混入推荐流。

这部分语义由插件本地决定，不依赖小黑盒服务端提供“多分区合并”能力。

## Plugin Page 真实分区探测

Dashboard API：

```text
GET /<plugin>/feed/topics/probe?profile_id=<id>[&topic_id=<id>]
```

行为：

1. 校验已认证 Dashboard 会话和 `profile_id`；
2. 读取真实分区目录；
3. 候选验证顺序为：显式请求的 topic → 已配置 topic → 目录前若干项；
4. 最多尝试 5 个 topic；
5. 每个成功样例最多返回 3 条帖子 ID / 标题 / 创建时间；
6. 返回 `read_only=true`。

该探测不调用 AI，不执行评论 POST，不修改小黑盒账号。

## 评论写入与发送状态

真实评论使用：

```text
POST https://workshopapi.xiaoheihe.cn/bbs/app/comment/create
```

插件的写入安全语义比 HTTP 成功/失败更严格：

- 已有成功发送记录：禁止第二次 POST；
- 网络、5xx、响应结构异常或任务在 POST 后取消：进入 `send_unknown`；
- `send_unknown` 先通过近期机器人评论尝试核对，不盲目重复发送；
- 明确业务失败：记录失败终态或仅在受支持错误类型下调度有限重试；
- 主动审核使用原子 claim 和账号级锁，避免并发批准重复评论。

用户真实运行历史已经验证过主动浏览 → AI → 候选/直发 → 小黑盒真实评论的整体链路；但非公开接口仍可能随小黑盒更新变化，因此新账号/大版本升级后建议先 dry-run。

## 当前验证边界

### 已有自动化覆盖

- 二维码状态与凭证字段；
- mention/reply 请求参数和已知消息类型；
- 帖子/楼层解析；
- 推荐流参数与本地分类；
- 真实分区目录递归解析；
- `topic_id` 校验与真实分区请求参数；
- 多分区去重/排序/部分失败/全部失败回退；
- Plugin Page 只读探测；
- 评论发送成功/失败/状态未知和重复发送闸门；
- 401/403/429/5xx/网络异常；
- 日志和响应脱敏。

### 仍需要真实环境持续观察

- 小黑盒未来是否调整真实分区目录/帖子流路径或字段；
- 分区目录是否因账号、地区或客户端版本出现差异；
- 评论字符上限和全部业务错误码语义；
- 评论图片上传（当前插件重点是接收/理解图片，不实现通用评论图片上传）。

任何真实接口变化都应先更新本文件、fixture/测试和 `endpoints.py` / parser 契约，再发布版本。

## 参考与许可

第三方行为参考和许可证说明见 [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md)。许可证未明确的项目仅用于观察外部行为和协议交互，本项目不复制其源码、特殊常量、注释或目录结构。
