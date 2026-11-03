# 给编码代理的说明

Cursor 会从 `.cursor/skills/<name>/SKILL.md` 自动加载本仓库的工程技能。来源、许可证和更新方式见 `.cursor/skills/README.md`。

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

`grill-with-docs` 与 `triage` 还会点名 `grilling`，`tdd` 还会点名 `codebase-design`。这两个技能没有安装。配置已经在 `docs/agents/`，不要去跑未安装的 `setup-matt-pocock-skills`。
