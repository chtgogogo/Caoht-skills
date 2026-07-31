# Engramory 记忆纪律（engramory-discipline）

把 AI 的长期记忆做成**文件化、可检索、可审计、召回高质量、且不撑爆上下文**的知识库。
一套"存—整—召"全周期纪律，可被任何支持 Skill 的 AI 助手（如 WorkBuddy）自动调起。

## 它解决什么
- **记忆不能只存不理**：提供整理/合并/归档协议，防止记忆越积越乱、召回变臭。
- **召回质量**：按类型亲和 + 关键词 + 时效排序，精准召回少数相关记忆，而不是全量塞进上下文。
- **省 token（指针指引）**：`MEMORY.md` 只存指针（每行一个），每轮只读索引、按需懒加载详情文件，不批量读全部记忆。
- **可控可审计**：人工维护为主，feedback/project 类强制写 `Why` / `How to apply`；多 agent 共享时矛盾必须问人。

## 目录结构
```
<root>/                  # ENGRAMORY_ROOT，默认 ~/.engramory/
  MEMORY.md              # 索引：只存指针，每轮唯一必读文件
  memory/<type>/<slug>.md  # 详情，按 feedback/project/user/reference 分目录
  archive/               # 冷记忆归档（不删，仍可检索）
  ledger.md              # 多 agent 同步账本（可选）
```

## 安装
- 方式 A（推荐）：把本目录作为 skill 放入你的 skills 文件夹（如 `~/.workbuddy/skills/engramory-discipline/`），引擎按 SKILL.md 的 `description` 自动触发。
- 方式 B：直接安装打包好的 `.skill` 文件（若有）。

## 配置（可选）
- `ENGRAMORY_ROOT`：全局记忆根目录，默认 `~/.engramory/`。
- `ENGRAMORY_SHARED_ROOT`：多 agent 共享记忆根（默认复用 `ENGRAMORY_ROOT`）。
- 索引上限：`ENGRAMORY_WARN` / `ENGRAMORY_HARD`（行数）、`ENGRAMORY_WARN_BYTES` / `ENGRAMORY_HARD_BYTES`（字节）。

## 配套脚本
| 脚本 | 作用 |
|---|---|
| `engramory_recall.py "<query>" [--top 5] [--print]` | 排序召回，省 token（推荐每轮用它代替全读索引） |
| `engramory_organize.py [--apply] [--older-than 90]` | 整理/归档（默认 dry-run，只报不改） |
| `engramory_check.py <MEMORY.md>` | 索引大小校验（OK/WARN/OVER） |
| `engramory_doctor.py` | 全量体检（重复/过时/超大/断链） |

## 许可
MIT（与 Caoht-skills 仓库一致）。可自由使用、修改、再分发，保留版权声明即可。
