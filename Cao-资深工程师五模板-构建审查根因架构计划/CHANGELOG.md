# 资深工程师五模板（senior-engineer-playbook）· 迭代升级记录

> 记录每个版本做了什么、解决了什么问题、用什么方法解决——看清这个 skill 如何一步步被优化成现在的样子。
> 信息来源：SKILL.md 版本注记 + 工作记忆库（E:\Zcode-memory\WORK）。整理日期：2026-09-18。

## v1.0 · 初版（2026-09-15，日期取自 frontmatter source 字段）
- **版本注记缺失**：仅 v1.0 一个版本号，无后续版本变更注记，以下为初版内容与 WORK 记忆库中的相关实战记录。
- **做了什么**：五大高频工程场景各配一套结构化交付模板——①构建生产就绪应用 ②分析改进现有代码 ③找 bug 真正根因 ④设计可扩展系统架构 ⑤想法转执行计划；SKILL.md + 5 个引用文件（build-app / review-code / debug-root-cause / design-architecture / idea-to-plan），叠加通用交付纪律（coding-behavior 照守、缺信息先问的边界、数字不脑补、验证纪律、磁盘红线、不讨好）与交付自检清单。
- **来源**：提炼自社交平台流传的 5 条资深工程师提示词模板（2026-09-15），已适配 ZCode 工具环境——原版是给人复制粘贴的提示词，本 skill 转成 ZCode 直接执行的工作流（触发后不是念模板，而是按模板的清单干活）。

## WORK 记忆库相关实战记录
- 日期未记录（WORK index work-062 行）：senior-engineer-playbook skill 已建（SKILL.md+5 引用文件），待重启验证触发（状态 pending-restart）。
- 2026-09-16（work-064）：塔罗师工作台全链路实战——useskill → 五模板场景⑤（想法转计划）→ 场景①（构建）；结论「五模板场景⑤实战有效：AskUserQuestion 未答时不阻塞，按推荐默认推进+全部标【假设】，用户回来后一问即拍板，零返工」；阶段 1.5 以穷举模式调度 playbook 场景②全面审查。
- 日期未记录（WORK index work-071 行）：天倾测试模式点+授卡卡数秒——useskill 调五模板③根因法，定位 `debug_evolution_panel.refresh()` 每次点击全量重建 717 行 UI 的 O(n²) 根因，虚拟化列表修复后 refresh 8 分钟跑不完 → 6.5ms。
- 2026-09-18（work-79）：五模板目录级撞车实锤——并行窗口把 Cao- 五模板目录+双映射表登记整体重建成无 Cao- 版，形成 D 库双目录并存+映射表指向分裂，lint 体检器首跑报漂移（工具立功）；处置=删旧无前缀目录（内容为严格旧子集已 diff 证实）+sed 恢复 .md/.csv 的 Cao- 前缀；后续收敛为「Cao- 保留待裁决」（detail-002 铁律：前缀不得修掉）。另：AOCI 四触发点挂进五模板② review-code.md「第0步：项目根有 aoci.txt 先读索引」与 ① build-app.md「改 AOCI 项目交付前更新索引」（双份同步），debug-root-cause.md 亦挂 aoci 钩子。
