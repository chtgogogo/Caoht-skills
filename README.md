# Caoht Skills

> 我 个人维护的 AI Agent Skill 集合，适用于任何读取 `SKILL.md` 的 agent 框架。

这里收录我日常使用、打磨过的 skill。每个 skill 以**独立英文子文件夹**存放：`SKILL.md` 是该 skill 的核心指令（AI 读取），子文件夹内自带 `README.md` 作为给人看的使用门面。

## 收录的 Skills

| Skill | 一句话简介 | 文档 |
|---|---|---|
| [multi-agent-team-governance](multi-agent-team-governance/README.md) | 多角色（多个 AI agent + 你本人）协作的**纯文档化**治理方法论——防撞车、防被重复追问，含编辑锁/统一决策/信箱/交接四条纪律与治理体检脚本 | [查看](multi-agent-team-governance/README.md) |
| [use-skill](use-skill/README.md) | Skill **统一调度入口**：按名快查、按分类索引检索、本地无匹配转市场检索并经安全审计安装；含调用异常降级与透明性报告 | [查看](use-skill/README.md) |
| [github-repo-extract](github-repo-extract/README.md) | 检索并提炼 GitHub 仓库有用内容的标准工作流：gh CLI 鉴权拉取、5 维度价值判断、License 红线安全下载、沉淀分析文档 | [查看](github-repo-extract/README.md) |
| [personal-mentor](personal-mentor/README.md) | 统一的贴身导师：深度摸底 → 专属学习框架与路线 → 极细教学 + 主动练习 + 5 级掌握度跟踪，跨会话延续 | [查看](personal-mentor/README.md) |

> 仓库持续扩展中：新增 skill 时请遵循下方仓库结构。

## 仓库结构

```
Caoht-skills/
├── LICENSE              # MIT（Copyright 2026 CaoHT）
├── README.md            # 本文件（仓库总索引）
├── .gitignore
└── <skill-name>/        # 每个 skill 一个英文子文件夹，名字须与 SKILL.md 的 name 一致
    ├── SKILL.md         # 必需：AI 执行的核心指令与触发规则
    ├── README.md        # 推荐：给人类看的使用门面
    └── references/      # 可选：模板 / 参考文档
```

## 如何安装某个 Skill

将对应 skill 子文件夹**整体复制**到 agent 的 skills 目录，重启即可：

```bash
# 以 WorkBuddy 为例
cp -r multi-agent-team-governance ~/.workbuddy/skills/
# 重启 WorkBuddy 后该 skill 即生效
```

或参考各 skill 子文件夹内的 `README.md` 获取专属安装 / 使用说明。

## 协议

[MIT License](LICENSE) —— Copyright 2026 CaoHT。
