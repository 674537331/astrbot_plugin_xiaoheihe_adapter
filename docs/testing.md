# 测试说明（v1.3.1）

## 最近一次完整验证

日期：**2026-08-24**

v1.3.1 发布复核分支的完整质量任务结果：

```text
280 passed
TOTAL 4007 statements / 1300 branches
branch coverage: 83%
```

关键模块覆盖率：

```text
xiaoheihe/config_service.py   84%
xiaoheihe/feed_service.py     83%
xiaoheihe/topic_service.py    91%
xiaoheihe/repository.py       88%
xiaoheihe/security.py         83%
```

同一轮还通过：

- `python tools/validate_repository.py`；
- Ruff lint；
- Ruff format check；
- `python -m compileall -q .`；
- `node --check pages/xiaoheihe/app.js`；
- `node --check pages/xiaoheihe/topic_probe.js`；
- AstrBot API stub import smoke；
- AstrBot 4.24.2 兼容检查；
- AstrBot 4.26.2 兼容检查；
- 当前支持范围内最新稳定 AstrBot 兼容检查；
- Dependency Review；
- Secret Scan；
- CodeQL Python / JavaScript-TypeScript（以最终 PR 检查结果为准）。

## 本地执行

```bash
python -m pip install -e ".[test]"
ruff check .
ruff format --check .
python -m compileall -q .
node --check pages/xiaoheihe/app.js
node --check pages/xiaoheihe/topic_probe.js
python tools/validate_repository.py
coverage run -m pytest -q
coverage report
```

CI 使用 Python 3.12 执行核心测试，并额外安装不同 AstrBot 版本做包级 API 契约检查。

## v1.3.0 新增重点测试

### 1. 真实分区浏览

覆盖：

- 真实分区目录递归解析；
- `topic_id` 只允许 1–32 位数字；
- `/bbs/app/topic/feeds` 参数和分页限制；
- 最多 20 个分区选择；
- 多分区有界并发；
- 跨分区帖子去重；
- 按创建时间 / 热度排序；
- 单个分区失败时继续其他分区；
- `CancelledError` 继续向上传播，并由回归测试确认不会误触发推荐流回退；
- 所有真实分区失败时才读取一次推荐流回退；
- 任一真实分区成功时不混入推荐流。

### 2. 浏览来源 Plugin Page

覆盖：

- 独立 `浏览来源` 标签存在；
- 真实分区只读探测 API；
- 最多尝试有限候选分区验证帖子流；
- 返回目录、已配置 topic、样例帖子与 feed 验证状态；
- 页面支持真实分区多选；
- 页面支持名称 / 分组 / topic_id 搜索；
- 回退推荐流分类支持多选；
- 真实分区启用时回退控件置灰；
- `topic_ids` / `fallback_sources` 通过同一 `config/save` 持久化；
- 浏览来源保存后刷新管理页，避免普通设置页继续持有旧完整配置快照并覆盖刚保存的来源字段；
- 真实分区选择从超过 20 个恢复到限制内时，超限错误状态同步恢复。

### 3. v1.2.x → v1.3.0 配置迁移

特别覆盖 AstrBot schema 更新顺序：

```text
旧配置 source=hardware
  + AstrBot 根据新 schema 先补 fallback_sources=["all"]
  → ConfigService
  → fallback_sources=["digital_tech"]
```

同时验证：

- 新的非默认 `fallback_sources` 优先于残留旧 `source`；
- `source` 在迁移后从插件运行配置中移除；
- `_conf_schema.json` 仍持久化 `source` / `topic_ids` / `fallback_sources`，三者均为 `invisible`；
- Plugin Page 通用“设置” schema 会移除三者，避免与独立“浏览来源”页重复编辑。

### 4. 发布一致性

`tools/validate_repository.py` 新增硬检查：

- `metadata.yaml` 版本；
- `pyproject.toml` 版本；
- `xiaoheihe.__version__`；
- README “当前版本”；
- CHANGELOG 第一条版本；
- Bug Report 模板默认插件版本；
- `web_api.py` 禁止重新出现硬编码诊断版本；
- 浏览来源三个 schema 持久化字段必须存在、隐藏且默认值正确；
- `_conf_schema.json` 的所有运行配置组、字段与默认值必须和 `DEFAULT_CONFIG` 一致。

这类遗漏现在会直接让 CI 失败，而不是等发布后人工发现。

## 既有回归范围

完整 pytest 仍覆盖以下长期能力：

- 二维码登录、状态查询、凭证失效和安全登出；
- mention/reply 分页、首次历史基线、跨轮 backfill 和游标恢复；
- 帖子/楼层/评论解析和富文本图片；
- 黑白名单、主人权限、缺 UID 的 fail-closed 身份处理；
- 同楼层多人 sender 身份持久化；
- 长楼层焦点路由和 LLM 压缩降级；
- 图片 Provider 路由、预算、缓存、归属校验和 24 小时视觉快照；
- Grok 普通搜索图片隔离与显式搜图兼容；
- Agent begin/done、工具调用、流式/分段回复聚合；
- dry-run、人工审核和无审核主动直发三种路径；
- outgoing 幂等、`send_unknown`、近期评论核对和取消语义；
- SQLite 迁移、清理、软上限与日志脱敏；
- Plugin Page 配置、浏览来源、事件、候选、日志 SSE、存储和诊断 API。

## 外部集成测试

真实账号测试默认不执行。需要显式：

```text
XHH_INTEGRATION_TEST=1
```

真实账号集成验证应保持 dry-run，除非测试目标本身就是已确认的评论写入路径。禁止在测试日志、fixture、Issue 或 CI artifact 中保存 Cookie、Token、二维码、设备 ID、真实私人正文或其他未脱敏账号数据。

## 测试与真实接口的边界

Mock/fixture 测试用于锁定**本项目认为正确的请求与响应契约**，不能证明小黑盒未来不会调整非公开 API。因此：

- 小黑盒接口变化需要同时更新 `docs/xiaoheihe-api-contract.md`；
- 新增/修改响应字段必须增加 parser fixture；
- 真实分区路径变化必须增加 topic service 与回退行为测试；
- 发送路径变化必须同时验证幂等和 `send_unknown`，不能只测试 HTTP 200。
