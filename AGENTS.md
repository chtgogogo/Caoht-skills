# 本仓库 Agent 工作规范（自动加载）

任何 AI Agent 在本仓库内工作时，必须遵守：

1. **新增或修改任何 skill 前**，先完整阅读 [CONTRIBUTING.md](CONTRIBUTING.md) 并遵循其全部规范——特别是：
   - SKILL.md frontmatter 必须有 `name` / `description` / `version` 三件，另加 `metadata.displayName`（中文展示名，唯一权威源；`version` 推荐放顶层，写在 `metadata` 下同样识别）；
   - **skill 目录名 = frontmatter 的 `name`**（英文，开放标准硬要求，不一致判红）；中文展示名走 `displayName`／README「中文名」列／`docs/` 详解页文件名与页内标题三处，不许把中文塞回目录名；
   - README 收录行必须用"卖点句式"（解决什么 + 凭什么），禁止空话；收录表现在是五列（Skill／**目录**／**中文名**／版本／说明），「目录」列是对账键、「中文名」列必须逐字等于 `metadata.displayName`；
   - 必须创建 `docs/<中文详解页名>.md` 详解页（六要素模板 + 顶部/底部返回总目录的双向跳转链接，锚点 `README.md#skills-index`；详解页文件名保留中文，**不跟随英文目录名**）；skill 目录内若有 `references/` / `assets/` / `scripts/`，详解页还要加「深入阅读」节，用相对链接把它们挂出来（链接而非复制）；
2. **权威库改完必须把仓库版追平再 push**：同步的是内容与版本——版本号只认权威库 `SKILL.md` frontmatter 的 `version`，库内新增/删除的文件（`references/`、`tools/`、`scripts/`）同步增删，不许只改主文件；追平后**在仓库侧脱敏**（四类红线见 CONTRIBUTING 第一节），未脱敏的副本禁止 push；权威库保留真实路径，不在那边脱敏。同步器按 frontmatter `name` 决定落盘目录名（`--dst auto`），权威库的中文目录名→本仓库的英文目录名由库侧替换表逐条翻译，两端映射只记在权威库的《__SKILL详细名映射表.md》。
3. **交付前必须运行** `py library_check.py` 且全部通过；改过体检器或收录表列布局时，先跑变异探针证明每条新判据「能红」（基线不红、变异后红、退出码从 0 变 1），恒真判据等于没加；
4. 修改权威库侧 `A-Cao-` 基础设施类 skill 后，同步其他宿主副本并做 sha256 校验（`A-Cao-`／`Cao-`／`CHT-` 前缀体系属**权威库**，本仓库目录一律用 `name`，见 CONTRIBUTING 第一节）；
5. 不在权威库创建无前缀的 skill 目录；不复制产生重复副本——本仓库里同一支 skill 只允许一个目录（旧中文目录名与新英文目录名并存会被同步器拦下并报红）。
