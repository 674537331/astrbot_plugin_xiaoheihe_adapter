# 测试说明（v1.3.3）

## 最近一次完整验证

日期：**2026-08-24**

v1.3.3 上下文相关性与视觉路由优化的最终质量任务结果：

```text
287 passed in 6.48s
TOTAL 4113 statements / 1338 branches
branch coverage: 83%
```

关键模块覆盖率：

```text
xiaoheihe/context_builder.py       91%
xiaoheihe/context_compression.py   86%
xiaoheihe/context_relevance.py     90%
xiaoheihe/config_service.py        84%
xiaoheihe/feed_service.py          83%
xiaoheihe/topic_service.py         91%
xiaoheihe/repository.py            88%
xiaoheihe/security.py              83%
```

同一轮还通过：

- `python tools/validate_repository.py`；
- Ruff lint；
- Ruff format check（75 个 Python 文件）；
- `python -m compileall -q .`；
- `node --check pages/xiaoheihe/app.js`；
- `node --check pages/xiaoheihe/topic_probe.js`；
- AstrBot API stub import smoke；
- AstrBot 4.24.2 兼容检查；
- AstrBot 4.26.2 兼容检查；
- 当前支持范围内最新稳定 AstrBot 兼容检查；
- Dependency Review；
- Secret Scan；
- CodeQL Python / JavaScript-TypeScript。

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

## v1.3.3 新增重点测试

### 1. 楼层相关性路由

覆盖：

- `related` / `partial` / `unclear` 默认保留原帖，未知关系值也 fail-open；
- 只有语义压缩明确返回 `drifted` 时才允许省略低相关原帖文字和原帖图片摘要；
- 当前消息显式提到原帖、主帖、楼主、帖子内容或原帖图片时强制恢复原帖；模糊的“求原图”仍按最近图片语境处理，不错误恢复主帖；
- `drifted` 渲染仍完整保留当前消息、直接回复对象、局部楼层主题和参与者归属，内部关系标签不再以“已偏离原帖/歪楼”等自然语言注入最终模型；
- 长楼层压缩失败时继续回退确定性窗口，不阻断主回复。

### 2. 图片来源距离与归属

覆盖：

- 图片稳定优先级为“当前评论 → 直接回复对象 → 楼层锚点 → 原帖”；
- 当前评论图片很多时，直接回复对象和楼层锚点仍能先保留至少一个近距离代表图，低优先级原帖图不挤占本地图片槽位；
- 直接回复对象与楼层锚点图片能够从楼层树提取；楼层树命中目标但媒体字段缺失时，直接回复对象图片可安全回退同一通知 `comment_b` 的媒体；
- 新增来源携带独立所有者 UID、昵称、角色与 `comment:<id>` 本地身份键；
- 来源/角色/身份键不一致时继续 fail-closed；
- 当前评论和直接回复对象图片预处理失败时允许原生视觉兜底，楼层锚点与原帖低优先级图片仍保持受控降级。

### 3. 慢 Provider 时间预算与单次压缩

覆盖：

- 外层回复 timeout 独立预留 `context + vision + main + fallback` 预算；
- 图片预算计算会扣除上下文压缩预留，避免视觉链侵占主回复或 fallback 时间；
- 同一事件的长楼层压缩最多真正请求一次上下文 Provider；
- 长原帖 + 短楼层不会仅因完整原帖文本超过阈值而额外调用上下文 Provider；
- 前置压缩失败后，后续 `on_llm_request` 不会再次请求同一个慢 Provider；
- 不实现 `get_extra()` 的最小兼容测试事件仍可安全调用压缩逻辑。

### 4. 正常路径回归

覆盖：

- 普通短帖子/回复不因 v1.3.3 新增额外 LLM 判断；
- 正常与原帖相关的楼层继续保留原帖文字和原帖图；
- 帖子级消息、主动浏览、权限、AI 额度、审核、幂等发送、数据库和会话 ID 行为不变；
- 用户从局部话题重新明确引用原帖时可以立即恢复原帖上下文，不被之前的 `drifted` 状态锁死。

## v1.3.2 新增重点测试

### 1. 当前发送者身份去重

覆盖：

- `ContextBuilder` 的 runtime/community 临时背景不再重复展开当前发送者完整昵称 + UID；
- 当前发送者完整身份继续由非临时 `xiaoheihe_sender_identity` 负责共享楼层 Conversation 中的跨轮归属；
- 身份值仍标记为不可执行数据，并保持不同 UID / 本地身份锚点的第一人称隔离；
- 主 Agent 上下文存在“身份只用于归属和消歧，非必要不主动称呼/复述”的约束。

### 2. 原帖与长楼层身份去重

覆盖：

- 原帖作者完整身份在压缩上下文只展开一次；
- 压缩 Provider 只能返回预分配的 `speaker_key`，编造/未绑定 key 继续拒绝整份结果；
- `thread_items.summary` 不承担昵称、UID 或本地锚点复述；
- 有摘要的参与者只在逐人摘要行展开一次完整身份；
- 已经在直接回复对象原文中绑定的参与者，在参与者锚点只保留 `speaker_n` 引用；
- 未在其他必要来源绑定的参与者最多保留一次身份→`speaker_n` 映射；
- 当前发送者完整身份不在压缩社区块重复注入。

### 3. 图片身份与视觉摘要

覆盖：

- 图片压缩 prompt 仍携带程序绑定的所有者身份用于所有权判断；
- 压缩器被明确要求不要把所有者昵称、UID、角色、本地身份锚点写入视觉摘要；
- 最终图片上下文保留 UID 所有权判断与“你发的图片”安全边界，同时限制非必要身份复述；
- 既有图片归属错位、未知所有者、跨帖子缓存和 fail-closed 测试继续通过。

### 4. 主动浏览来源措辞

覆盖通用主动事件不再被固定描述成“主动浏览推荐流”。真实分区与推荐流回退仍使用原有请求、筛选、AI 额度、审核和发送路径。

## v1.3.x 真实分区重点测试

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

### 3. v1.2.x → v1.3.x 配置迁移

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

`tools/validate_repository.py` 硬检查：

- `metadata.yaml` 版本；
- `pyproject.toml` 版本；
- `xiaoheihe.__version__`；
- README “当前版本”；
- CHANGELOG 第一条版本；
- Bug Report 模板默认插件版本；
- `web_api.py` 禁止重新出现硬编码诊断版本；
- 浏览来源三个 schema 持久化字段必须存在、隐藏且默认值正确；
- `_conf_schema.json` 的所有运行配置组、字段与默认值必须和 `DEFAULT_CONFIG` 一致。

这类遗漏会直接让 CI 失败，而不是等发布后人工发现。

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
