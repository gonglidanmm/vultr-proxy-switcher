# 给编码代理的说明

Cursor 会从 `.cursor/skills/<name>/SKILL.md` 自动加载本仓库的工程技能。来源、许可证和更新方式见 `.cursor/skills/README.md`。

业务规则和数据模型以 `docs/系统需求与设计文档.md` 为准（功能、业务规则、数据模型、关键决策、已知坑）。代码只说明怎么实现。`GLOSSARY.md` 和 `docs/adr/` 只补充术语和难逆转的取舍，不覆盖这份文档。结束一次改动之前运行 `uv run pytest tests`。不要在仓库根目录裸跑 `uv run pytest`：`scripts/` 里文件名带 test 的脚本会被收进去，`scripts/assign_yong_test.py` 在 import 时直接退出。

## Agent skills

### Issue tracker

Issue 记在 GitHub Issues，仓库是 `gonglidanmm/vultr-proxy-switcher`。见 `docs/agents/issue-tracker.md`。

### Triage labels

五个分诊角色使用上游默认标签名。见 `docs/agents/triage-labels.md`。

### Domain docs

single-context。文档放在 `docs/`：ADR 在 `docs/adr/`，本段指向的代理配置在 `docs/agents/`。术语表是仓库根目录的 `GLOSSARY.md`（技能按这个路径读写）。见 `docs/agents/domain.md`。

## 何时使用

- 修 bug 时用 `diagnosing-bugs`：先做出能复现这条故障的反馈回路，再假设和修复。
- 新的纯逻辑用 `tdd`，例如 `app/relay.py` 这种端口、凭据和 `node_data` 计算。先红后绿，只测约定好的公开边界。
- 维护共享契约的术语表时用 `domain-modeling`：`node_data`、`config.json` 的键、端口偏移、区域表、分享链接格式。术语写入 `GLOSSARY.md`，难逆转的取舍写入 `docs/adr/`。设计还没说清时，人先输入 `/grill-with-docs`，再由它带动 `domain-modeling`。
- 把已经说清的结论变成 GitHub issue 时用 `to-tickets`，再按需要用 `triage` 推进标签。
- 结束一个 PR 之前用 `code-review`，对照仓库规范和对应的 issue 看 diff。
- 写代码时用 `ponytail` 决定写多少：先复用本仓库已有实现、标准库和已经安装的依赖，再写最少的新代码。不能为此删掉信任边界上的校验、防止丢数据的错误处理、安全措施，或有风险逻辑的测试。

`ponytail` 不改 `tdd` 和 `diagnosing-bugs` 的顺序。修 bug 仍先做 `diagnosing-bugs` 的反馈回路。新的纯逻辑仍走 `tdd` 的红-绿循环，并先约定 seam。`ponytail` 只在这两条流程里约束实现有多小。

两边说法冲突时：

- 测试以本仓库规则和 `tdd` 为准。`ponytail` 里「改动很小可以不写测试」或「留一个 assert 自检即可」不能代替 `tdd`，也不能代替 `diagnosing-bugs` 要求的回归测试。
- 回复格式以本仓库和用户的说明为准。`ponytail` 要求每条回复末尾写跳过了什么；和仓库或用户的回复要求冲突时，不按那一条收尾。

`/ponytail-audit` 没有安装，也不要默认去跑。它和分支 `cursor/architecture-analysis-00ee` 上的 `docs/ARCHITECTURE_ANALYSIS.md` 都是全库审查，那份文档已经覆盖了架构、入口和风险。需要另做一遍时再单独装，属于可选项。

`grill-with-docs` 与 `triage` 还会点名 `grilling`，`tdd` 还会点名 `codebase-design`。这两个技能没有安装。配置已经在 `docs/agents/`，不要去跑未安装的 `setup-matt-pocock-skills`。

## Cursor Cloud specific instructions

- Python 依赖：`uv sync --frozen --extra dev`（含 pytest、ruff、PyInstaller）。`uv` 在 `/usr/local/bin`。Vue 客户端在 `client/`，用 `npm ci`。不要跑 `wrangler deploy`，那会发布线上 Worker。
- 这里没有物理显示器。启动桌面端用 `xvfb-run -a uv run python -m app.main`。PySide6 需要 `libegl1`、xcb 相关库，以及 `fonts-noto-cjk`（界面是中文）。
- 本地 SOCKS5 转发和切换的自检：`uv run python scripts/test_localproxy.py`（占用 `127.0.0.1:1080`）。客户端开发服务器：在 `client/` 跑 `npm run dev`，地址 `http://127.0.0.1:1424`。
