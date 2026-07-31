# Caoht Skills

> 一份由个人长期打磨、可跨任意 agent 框架复用的 **AI Skill 工具箱**。

这里收录的每一个 skill，都是我在真实项目里反复使用、迭代过的「能干活的指令包」——不是 demo，而是能直接落地的生产级工具。每个 skill 以**独立英文子文件夹**存放：

- `SKILL.md` —— 给 AI 看的核心指令（触发条件、流程、红线），agent 自动读取；
- `README.md` —— 给人看的使用门面（能解决什么、怎么装、注意事项）；
- 需要时附带 `references/`、`scripts/` 等辅助文件。

---

## 收录的 Skills

| Skill | 一句话定位 | 当前版本 | 文档 |
|---|---|---|---|
| [use-skill](use-skill/) | **Skill 统一调度入口**：按名快查、分类检索、本地无匹配转市场并经安全审计安装 | v1.2 | [README](use-skill/README.md) |
| [multi-agent-team-governance](multi-agent-team-governance/) | 多角色（多个 AI agent + 你）协作的**纯文档化**治理：防撞车、防重复追问 | v1.1 | [README](multi-agent-team-governance/README.md) |
| [github-repo-extract](github-repo-extract/) | 检索提炼 GitHub 仓库的标准工作流：gh 拉取、5 维价值判断、License 红线安全下载 | v1.1 | [README](github-repo-extract/README.md) |
| [personal-mentor](personal-mentor/) | 统一贴身导师：深度摸底 → 定制路线 → 极细教学 + 掌握度跟踪，跨会话延续 | v1.0 | [README](personal-mentor/README.md) |
| [engramory-discipline](engramory-discipline/) | 文件化长期记忆的"存—整—召"全周期纪律：指针索引省 token、排序召回保质、定期整理归档、多 agent 共享 | v1.0 | [README](engramory-discipline/README.md) |

> 仓库持续扩展中。新增 skill 时请遵循下方「仓库结构」约定。

---

## 怎么装一个 Skill

把对应 skill 的**整个子文件夹**复制到你的 agent skills 目录，重启 agent 即可：

```bash
# 以 WorkBuddy 为例（其它框架换成对应的 skills 目录）
cp -r use-skill ~/.workbuddy/skills/
# 重启 WorkBuddy 后该 skill 即生效
```

更详细的安装 / 使用说明，见每个 skill 子目录内的 `README.md`。

---

## 版本与发布

每个 skill **独立版本、独立 Release**，互不捆绑——你可以只升级其中一个，而不必动其余。

- 所有 Release 与 tag 都在仓库的 [Releases 页面](https://github.com/chtgogogo/Caoht-skills/releases)。
- tag 命名规则：`<skill-name>-v<version>`（例如 `use-skill-v1.2`）。
- skill 的版本号同时写在各自 `SKILL.md` 的 `version:` 字段，方便离线核对。

---

## 仓库结构

```
Caoht-skills/
├── LICENSE              # MIT（Copyright 2026 CaoHT）
├── README.md            # 本文件（仓库总索引）
├── .gitignore
└── <skill-name>/        # 每个 skill 一个英文子文件夹，名字须与 SKILL.md 的 name 一致
    ├── SKILL.md         # 必需：AI 执行的核心指令与触发规则（含 version 字段）
    ├── README.md        # 推荐：给人类看的使用门面
    └── references/      # 可选：模板 / 参考文档 / 脚本
```

---

## 协议

[MIT License](LICENSE) —— Copyright 2026 CaoHT。

可以商用、修改、再分发，只需保留版权声明与 LICENSE 文件。
