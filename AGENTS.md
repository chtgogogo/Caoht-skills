# 本仓库 Agent 工作规范（自动加载）

任何 AI Agent 在本仓库内工作时，必须遵守：

1. **新增或修改任何 skill 前**，先完整阅读 [CONTRIBUTING.md](CONTRIBUTING.md) 并遵循其全部规范——特别是：
   - SKILL.md frontmatter 四件套（`name` / 顶层 `version` / `description`）；
   - README 收录行必须用"卖点句式"（解决什么 + 凭什么），禁止空话；
   - 必须创建 `docs/<skill文件夹名>.md` 详解页（六要素模板 + 顶部/底部返回总目录的双向跳转链接，锚点 `README.md#skills-index`）；
2. **交付前必须运行** `py library_check.py` 且全部通过；
3. 修改 `A-Cao-` 基础设施类 skill 后，同步其他宿主副本并做 sha256 校验；
4. 不创建无前缀的 skill 目录；不复制产生重复副本。
