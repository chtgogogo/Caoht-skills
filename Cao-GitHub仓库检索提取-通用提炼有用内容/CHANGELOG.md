# GitHub 仓库检索提取（通用）· 迭代升级记录

> 记录每个版本做了什么、解决了什么问题、用什么方法解决——看清这个 skill 如何一步步被优化成现在的样子。
> 信息来源：SKILL.md 版本注记 + 工作记忆库（E:\Zcode-memory\WORK）。整理日期：2026-09-18。

## v1.1（日期未记录）
- **做了什么**：未记录具体变更——SKILL.md 仅标注版本号（frontmatter `version: 1.1.0`、正文「版本：v1.1」），并定位为「通用 SOP，适用于任何工程类型」；天倾项目专用配置拆分为独立技能 `github-repo-extract-tianqing`（项目配置文件，不随本通用版发布）。
- **解决了什么**：未记录（无 v1.0→v1.1 变更注记）。
- **怎么解决的**：未记录。

## v1.0 · 初版
- **做了什么**：建立 GitHub 仓库检索提炼标准工作流：识别工程上下文 → gh CLI 鉴权查询（元数据 / 目录树 / README / 关键源码，兜底直连 api.github.com）→ 5 维度价值判断矩阵（可直接使用 / 可提取转译 / 设计提炼 / 架构分层 / 视觉规范）→ License 红线下载决策矩阵（含 licenseInfo 为 null 时查根目录 LICENSE 文件、禁止商用素材绝不纳入）→ 文档更新 → 编码注意（中文编码验证）→ 完成检查清单。
- **来源**：未记录（SKILL.md 未写提炼自什么实践）。

## WORK 记忆库相关实战记录
- 2026-09-12（work-059）：天倾 GitHub 参考第三轮深扫——useskill 穷举命中本技能的天倾专用版 `github-repo-extract-tianqing`（专用配置：四文档回写位 + 已分析 21 仓清单 + 命名规则），按其规约回写完成。
- 2026-09-14（work-004）：第一波 Cao8 重点 skill 升级——github-repo-extract（+metadata.version+边界），10/10 quick_validate 通过。
