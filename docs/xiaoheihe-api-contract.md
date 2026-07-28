# 小黑盒 API 契约与验证状态

## 重要声明

小黑盒没有为本项目提供稳定、公开的机器人 API 契约。v1.0.0 的接口行为参考
`SomeOvO/xhhRobot` 的功能思路，并由本项目使用 Python 独立实现。

调查时该参考仓库根目录没有发现明确许可证文件，因此本项目没有复制其源码、特殊常量、
注释、目录结构或请求实现。下面只记录观察到的功能端点名称和本项目自己的解析契约。

所有小黑盒网络端点目前都**没有使用真实账号完成验证**。自动测试只使用脱敏 fixture 和
`httpx.MockTransport`。在真实环境验证前必须保持 `dry_run: true`；本文不会把 fixture
测试描述成真实可用性证明。

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
- 成功响应不完整时，用相同 HTTP 会话请求 `restore_login`，合并两次响应后再校验。

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
的一次性签名尚未通过真实账号验证，因此必须继续保持 dry-run，不能把 Mock 成功等同于真实
评论发送可用。

## 端点清单

| 功能 | 方法与路径 | 参考观察 | 本项目状态 |
| --- | --- | --- | --- |
| 获取二维码 | `GET /account/get_qrcode_url/` | 参考项目出现该路径 | fixture 已测，待真实验证 |
| 查询扫码状态 | `GET /account/qr_state/` | 参考项目出现该路径 | fixture 已测，待真实验证 |
| 恢复完整登录态 | `GET /account/restore_login` | 官网兼容流程观察 | fixture 已测，待真实验证 |
| 当前账号 | `GET /account/info/` | 本项目隔离契约 | fixture 已测，路径和字段待真实验证 |
| @/回复通知 | `GET /bbs/app/user/message` | 参考项目出现该路径 | offset 分页、消息类型筛选和多形状解析已测，待用户复测 |
| 帖子与评论树 | `GET /bbs/app/link/tree` | 参考项目出现该路径 | fixture 已测，字段待真实验证 |
| 创建评论 | `POST https://workshopapi.xiaoheihe.cn/bbs/app/comment/create` | 参考项目出现该主机与路径 | 表单和响应 Mock 已测，动态签名与真实限制待验证 |
| 近期评论核对 | `GET /bbs/app/comment/user` | 本项目隔离契约 | Mock 已测，路径、排序和一致性待真实验证 |
| 主动帖子流 | `GET /bbs/app/feeds` | 本项目隔离契约 | Mock 已测，来源参数和字段待真实验证 |

所有路径只存在于 `xiaoheihe/endpoints.py`，所有字段兼容处理只存在于
`xiaoheihe/parsers.py`。真实环境出现结构变化时，应新增脱敏 fixture、修改解析层并回归，
不能把字段判断散落到适配器或业务服务。

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

缺少 `post_id`、稳定通知/评论标识、发送者 UID 或有效内容时，解析层抛出
`ResponseShapeError`，API Client 转换为 `ResponseContractError`。不会生成随机 ID 掩盖
上游契约变化。

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
