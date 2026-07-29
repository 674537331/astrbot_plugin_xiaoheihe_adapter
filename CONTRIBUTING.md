# 贡献指南

开发环境使用 Python 3.12–3.14。安装测试依赖后运行：

```bash
python -m ruff check .
python -m ruff format --check .
python -m pytest
python -m coverage run -m pytest
python -m coverage report
```

真实账号集成测试必须由环境变量 `XHH_INTEGRATION_TEST=1` 显式开启，且必须保持 dry-run；禁止提交任何凭证或未脱敏响应。

建议为 GitHub `main` 分支启用保护：禁止直接推送和 force push，PR 必须通过 CI 与 CodeQL，不得存在高危扫描结果，并至少获得一次审查。

每次版本发布都要同步更新 `README.md` 和 `CHANGELOG.md`。提交接口适配变更时，请同时更新
`docs/xiaoheihe-api-contract.md`、fixture 和解析测试。参考许可证未明确的实现时，以外部
行为和协议交互为研究范围；源码实现、特殊常量、注释和目录结构保持独立。
