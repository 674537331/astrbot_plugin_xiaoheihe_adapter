# 测试说明

## 本地命令

```bash
python -m pip install -e ".[test]"
ruff check .
ruff format --check .
python -m coverage run -m pytest -q
python -m coverage report
python -m compileall -q .
```

测试默认不访问网络、不需要小黑盒账号、不读取真实凭证。HTTP 行为使用
`httpx.MockTransport`，外部响应使用 `tests/fixtures/` 下的脱敏 JSON。

## 覆盖范围

- 二维码获取、等待、成功、过期、凭证保存/恢复/删除和登录失效；
- 通知解析、分页、首次基线、可选回溯、有界队列、并发唯一约束；
- 按账号/通知类型持久化 `message_id` 边界、缺省时间历史过滤和游标原子推进；
- 队列拥塞先持久化、SQLite 到期重试主动恢复和更新后状态继承；
- 自身消息、黑白名单、主人 UID 和管理员映射默认关闭；
- 当前评论/原帖/楼层上下文、HTML 清洗、临时上下文、双方图片 URL、固定图片 Provider 与视觉降级提示；
- SSRF 基础防护、路径穿越、日志/响应脱敏、回复清理与长度；
- 确定性 session/message ID、唤醒标记、父类发送状态；
- 模拟运行、流式聚合、一次事件一次真实发送；
- 成功发送、发送状态未知、近期评论确认、不盲重试；
- 明确 `status=failed` 终态、已有发送记录闸门和重启后 `dispatched` 隔离；
- 401/403 熔断、429 `Retry-After`、5xx 与网络重试；
- 主动帖子筛选、候选、批准、拒绝、并发批准闸门和每日上限；
- SQLite WAL、迁移、索引、事务幂等、恢复、清理和计数；
- 配置同步、保存成功提示、保存回滚、Plugin Page API、SSE、任务关闭和 HTTP Client 关闭；
- 覆盖更新后的适配器实例重建、冷启动不重复加载和配置实例状态回显；
- 平台注册与 `PlatformMetadata` 均使用仓库 `logo.png`；
- 主模块只注册平台适配器；固定 LLM Provider 通过事件选择进入 AstrBot 原生链路，固定图片 Provider 仅生成临时图片描述。

Coverage 启用分支统计，综合门槛为 80%。适配器、事件和 Web API 由专门的契约测试覆盖，
但因为运行时必须由 AstrBot 注入模块而不计入核心 coverage 分母；CI 另执行真实 AstrBot
发布包的文件/符号契约检查。

## 2026-08-06 v1.2.10 本地结果

- Pytest：`184 passed`；
- Coverage：`81%`（启用 branch，达到 `fail_under = 80`）；
- Ruff Check、Ruff Format Check、Python compileall、前端 `node --check`、仓库静态校验与 `git diff --check`：通过；
- 带图普通 Grok 网页查询会临时隔离原图并在工具结果后恢复，明确图片搜索与其他工具保持原图；
- 工具结果回调缺失时由 `on_agent_done` 兜底恢复图片，避免异常路径污染事件消息链；
- Grok 查询约束只在实际 `grok_web_search` 调用中修改该工具参数，其他工具参数保持原值；
- 包级契约新增 `on_using_llm_tool` / `on_llm_tool_respond`；
- AstrBot 4.24.2、4.26.2 和当前稳定版 4.27.2 的契约检查纳入发布验证。

## 2026-08-04 v1.2.9 本地结果

- Pytest：`181 passed`；
- Coverage：`81%`（启用 branch，达到 `fail_under = 80`）；
- Ruff Check 与 Ruff Format Check：通过；
- Python compileall、前端 `node --check` 和仓库静态校验：通过；
- 无工具调用的普通 Agent 回复、Grok 式工具状态/中间文本/最终回复、插件直发后继续 LLM、直接结果去重均有回归测试；
- AstrBot 自带任意段数回复、流式回复、一次事件一次真实发送、主动候选和无审核直发回归保持通过；
- AstrBot 4.24.2：12 个核心契约文件检查通过；
- AstrBot 4.26.2：13 个完整契约文件检查通过；
- 当前稳定版 AstrBot 4.27.1：13 个完整契约文件检查通过。

## 2026-08-03 v1.2.8 本地结果

- Pytest：`175 passed`；
- Coverage：`81%`（启用 branch，达到 `fail_under = 80`）；
- Ruff Check：通过；
- Ruff Format Check：通过；
- Python compileall：通过；
- `node --check pages/xiaoheihe/app.js`：通过；
- 仓库 JSON/YAML/静态文件/敏感运行文件检查：通过；
- 主动刷帖允许 `dry_run=false / review_required=false`，配置保存和插件构造不再抛出安全组合异常；
- 主动模式组合、合成事件分流和事件最终发送参数均有回归测试，无审核模式明确调用
  `deliver(..., dry_run=False, proactive=True)`；
- 默认主动刷帖关闭、模拟运行开启、人工审核开启，现有候选批准并发闸门保持不变；
- AstrBot 4.24.2：12 个核心契约文件检查通过；
- AstrBot 4.26.2：13 个完整契约文件检查通过；
- 当前稳定版 AstrBot 4.27.1：13 个完整契约文件检查通过。

## 2026-08-02 v1.2.7 本地结果

- Pytest：`172 passed`；
- Coverage：`81%`（启用 branch，达到 `fail_under = 80`）；
- Ruff Check：通过；
- Ruff Format Check：通过；
- Python compileall：通过；
- `node --check pages/xiaoheihe/app.js`：通过；
- 仓库 JSON/YAML/静态文件/敏感运行文件检查：通过；
- UTF-8 与连续乱码标记检查：通过；
- 浏览器交互：首屏懒加载、日志 SSE 生命周期、移动端单栏和主动审核确认均通过；
- 客户端池并发初始化回归：8 个并发调用只创建 1 个 HTTP Client、读取 1 次凭证；
- 评论 @ 类型 `17` 已覆盖“解析 → 轮询入队 → SQLite 事件记录 → 模拟运行完成”集成路径；
- 评论 @ 的原帖详情、指定楼层、通知原帖快照和双方图片合并路径已覆盖；
- 设置保存成功提示由前端契约检查和仓库静态检查共同覆盖；
- AstrBot 将完整模型结果拆成任意多次 `send()` 时，适配器恢复分段前文本、聚合为一次评论并显示管理页提醒；
- 多图事件基础超时、6 图自动扩展至 300 秒及 900 秒硬上限均有回归测试；
- 已完成事件、进程内重复通知、分页重复项和缺省时间历史通知的过滤回归测试通过；
- SQLite v4 消息边界、到期重试、重启隔离、更新继承和评论发送闸门回归测试通过；
- 主动候选并发批准只发送一次，更新或重启遗留 `sending` 转为 `send_unknown`；
- 覆盖更新后已启用适配器由 `Star.initialize()` 协调重建，冷启动保持 AstrBot 原生顺序；
- AstrBot 4.24.2：12 个核心契约文件检查通过（核心适配器范围）；
- AstrBot 4.26.2：13 个完整契约文件检查通过；
- 当前稳定版 AstrBot 4.26.8：13 个完整契约文件检查通过。

上述是单元、Mock、静态和包级契约结果，不代表真实小黑盒账号端到端测试。

## 真实集成测试

v1.0.0 不在普通 CI 中运行真实小黑盒集成测试，也不提交真实凭证。真实测试必须由维护者
显式准备独立测试账号并保持：

- 模拟运行开启（`dry_run: true`）；
- 主动刷帖关闭；
- 最小请求间隔不低于默认值；
- 测试后检查并删除脱敏前的临时诊断；
- 不把真实二维码、Cookie、Token、数据库或日志加入版本控制。

真实账号测试状态与准确边界见 `docs/xiaoheihe-api-contract.md`。

## CI

- `ci.yml`：语法、Ruff、格式、Pytest、Coverage、导入、AstrBot 版本兼容、包体积、
  禁止敏感运行文件、前端基础检查；
- `codeql.yml`：Python 与 JavaScript CodeQL；
- `dependency-review.yml`：PR 依赖审查；
- `secret-scan.yml`：Gitleaks；
- Dependabot：pip 与 GitHub Actions。
