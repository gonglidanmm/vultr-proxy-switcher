# 本仓库的 Agent Skills

这些文件是从 [mattpocock/skills](https://github.com/mattpocock/skills) 的 `main` 分支原样拷入的工程技能子集，供 Cursor 编码代理自动发现。

- 上游提交：[`4ae2317ddb22b046e26455e4f9c9b3aaaf62212d`](https://github.com/mattpocock/skills/commit/4ae2317ddb22b046e26455e4f9c9b3aaaf62212d)（2026-10-31）
- 上游路径：`skills/engineering/<skill-name>/`
- 许可证：MIT，Copyright (c) 2026 Matt Pocock。全文见同目录 [`LICENSE`](LICENSE)。修改或再分发这些技能时保留该版权声明与许可文本。
- 发现位置：`.cursor/skills/<skill-name>/SKILL.md`。Cursor 会递归扫描 `.cursor/skills/`，技能名以包含 `SKILL.md` 的目录名为准，且必须与 frontmatter 的 `name` 一致。

未拷贝各技能下的 `agents/openai.yaml`。那是 Codex 的调用策略，Cursor 不读。

`setup-matt-pocock-skills` 本身没有安装。已安装的技能只在仓库还没有 issue tracker / 标签配置时才叫人去跑它。本仓库的对应配置已经写在 `docs/agents/`，见 `AGENTS.md`。

## 已安装

| 技能 | 在本项目中的用途 |
| --- | --- |
| `domain-modeling` | 维护共享契约的术语表：`node_data`、`config.json` 的键、端口偏移、区域表、分享链接格式。说清的术语写入仓库根目录 `GLOSSARY.md`，难逆转的取舍写入 `docs/adr/`。 |
| `grill-with-docs` | 设计还没定下来时使用。追问到每个分支都有结论，并让 `domain-modeling` 把术语和 ADR 写进文档。人输入 `/grill-with-docs` 才会进入（`disable-model-invocation: true`）。 |
| `tdd` | 为新的纯逻辑写测试，例如 `app/relay.py` 里的端口与 `node_data`。先写失败测试，再写刚好能通过的实现，只测事先约定的公开边界。 |
| `diagnosing-bugs` | 修 bug。先做出能复现这条故障的反馈回路，再缩小现场、列出可证伪的假设、验证、补回归测试。 |
| `to-tickets` | 把已经说清的计划拆成可单独验证的 GitHub issue，写明阻塞关系，发布到 `gonglidanmm/vultr-proxy-switcher`。人输入 `/to-tickets` 才会进入。 |
| `triage` | 把 GitHub issue 在 `needs-triage`、`needs-info`、`ready-for-agent`、`ready-for-human`、`wontfix` 之间推进；需要代理接手时按 `AGENT-BRIEF.md` 写说明。人输入 `/triage` 才会进入。 |
| `code-review` | 结束一个 PR 之前使用。对照本仓库的编码规范，以及对应的 issue / spec，分别看同一份 diff。 |

## 如何更新

1. 在上游仓库查看比 `4ae2317ddb22b046e26455e4f9c9b3aaaf62212d` 更新的提交。
2. 只替换本目录里已经安装的技能：用 `skills/engineering/<skill-name>/` 覆盖 `.cursor/skills/<skill-name>/`，继续不要带上 `agents/openai.yaml`。
3. 若 `setup-matt-pocock-skills` 的模板有变，按同样的选择重写 `docs/agents/`，不要改技能正文来迁就本仓库：
   - issue tracker：GitHub Issues，仓库 `gonglidanmm/vultr-proxy-switcher`
   - triage labels：上游默认五个角色名
   - docs location：`docs/`（ADR 在 `docs/adr/`，代理配置在 `docs/agents/`；`GLOSSARY.md` 仍在仓库根目录，因为技能按这个路径读写）
4. 把本文件里的提交 SHA 改成新的上游提交。
5. 上游安装器是 `npx skills@latest add mattpocock/skills -a cursor`。它会交互选择技能，并可能装入全部技能和 `agents/openai.yaml`。只用它做对照；装完后删掉本表以外的技能和 `agents/` 目录，使结果与这次的子集一致。

## 未安装、但被引用的技能

这些引用留在上游原文里，没有额外安装：

- `grilling`：`grill-with-docs` 和 `triage` 会调用它。本仓库只能执行同一次调用里的 `domain-modeling`。
- `codebase-design`：`tdd` 在模块接口形状还没定时会查阅它。没有这份词汇表时，按 `tdd` 自己的 seam 规则继续。
- `setup-matt-pocock-skills`：`to-tickets`、`triage`、`code-review` 在读不到 tracker 配置时会提示去跑它。配置已在 `docs/agents/`，不要安装或重跑。
- `improve-codebase-architecture`：只出现在上游 `domain.md` 模板里。本仓库的 `docs/agents/domain.md` 已改成只指向已安装的 `grill-with-docs`。
