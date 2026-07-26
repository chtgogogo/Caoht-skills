# multi-agent-team-governance-skills

这是一个用于帮助多 agent 能够协作完成任务，相互之间确定分工，相互沟通的 skills，避免同一个项目下，不同的 agent 对同一个问题反复询问。

## 仓库结构

```
multi-agent-team-governance-skills/
├── LICENSE              # MIT（Copyright 2026 CaoHT）
├── README.md            # 本文件（仓库索引）
├── .gitignore
└── multi-agent-team-governance/   # 技能本体
    ├── SKILL.md         # AI 执行的核心指令与触发规则
    ├── README.md        # 技能门面（痛点 / 方案 / 安装 / 示例 / 能力 / FAQ）
    └── references/      # 模板：契约 / 决策看板 / 信箱说明
```

## 技能列表

- **multi-agent-team-governance** —— 多 Agent 团队协作文档治理。详见 [`multi-agent-team-governance/README.md`](multi-agent-team-governance/README.md)。

## 安装

将 `multi-agent-team-governance/` 文件夹复制到你的 agent skills 目录（如 `~/.workbuddy/skills/`），重启即可。
