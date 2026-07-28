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

## 端点清单

| 功能 | 方法与路径 | 参考观察 | 本项目状态 |
| --- | --- | --- | --- |
| 获取二维码 | `GET /account/get_qrcode_url/` | 参考项目出现该路径 | fixture 已测，待真实验证 |
| 查询扫码状态 | `GET /account/qr_state/` | 参考项目出现该路径 | fixture 已测，待真实验证 |
| 当前账号 | `GET /account/info/` | 本项目隔离契约 | fixture 已测，路径和字段待真实验证 |
| @/回复通知 | `GET /bbs/app/user/message` | 参考项目出现该路径 | 分页和多形状解析已测，参数/字段待真实验证 |
| 帖子与评论树 | `GET /bbs/app/link/tree` | 参考项目出现该路径 | fixture 已测，字段待真实验证 |
| 创建评论 | `POST /bbs/app/comment/create` | 参考项目出现该路径 | Mock 已测，参数、限制和返回字段待真实验证 |
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
