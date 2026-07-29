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
- 自身消息、黑白名单、主人 UID 和管理员映射默认关闭；
- 帖子/楼层上下文、HTML 清洗、临时上下文、图片 URL、视觉降级提示；
- SSRF 基础防护、路径穿越、日志/响应脱敏、回复清理与长度；
- 确定性 session/message ID、唤醒标记、父类发送状态；
- dry-run、流式聚合、一次事件一次真实发送；
- 成功发送、发送状态未知、近期评论确认、不盲重试；
- 401/403 熔断、429 `Retry-After`、5xx 与网络重试；
- 主动帖子筛选、候选、批准、拒绝和每日上限；
- SQLite WAL、迁移、索引、事务幂等、恢复、清理和计数；
- 配置同步、保存回滚、Plugin Page API、SSE、任务关闭和 HTTP Client 关闭；
- 主模块只注册平台适配器，模型调用统一进入 AstrBot 原生链路。

Coverage 启用分支统计，综合门槛为 80%。适配器、事件和 Web API 由专门的契约测试覆盖，
但因为运行时必须由 AstrBot 注入模块而不计入核心 coverage 分母；CI 另执行真实 AstrBot
发布包的文件/符号契约检查。

## 2026-07-29 v1.0.8 本地结果

- Pytest：`132 passed`；
- Coverage：`81%`（启用 branch，达到 `fail_under = 80`）；
- Ruff Check：通过；
- Ruff Format Check：通过；
- Python compileall：通过；
- `node --check pages/xiaoheihe/app.js`：通过；
- 仓库 JSON/YAML/静态文件/敏感运行文件检查：通过；
- 评论 @ 类型 `17` 已覆盖“解析 → 轮询入队 → SQLite 事件记录 → dry-run 完成”集成路径；
- AstrBot 4.24.2：10 个核心契约文件检查通过（核心适配器范围）；
- AstrBot 4.26.2：11 个完整契约文件检查通过；
- 当前稳定版 AstrBot 4.26.7：11 个完整契约文件检查通过。

上述是单元、Mock、静态和包级契约结果，不代表真实小黑盒账号端到端测试。

## 真实集成测试

v1.0.0 不在普通 CI 中运行真实小黑盒集成测试，也不提交真实凭证。真实测试必须由维护者
显式准备独立测试账号并保持：

- `dry_run: true`；
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
