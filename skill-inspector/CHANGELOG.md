# skill-inspector · 安装来源与安检记录

> provenance 台账：本支从哪来、装时做了哪些本地适配、入库前安检读数。上游没有 CHANGELOG，本库不替上游编史；上游变更自取。
> 本支入库依据：2026-10-17 useskill v2.7 整改（审计环节三名字统一为一支权威执行体）。

## v2.13.0 · 装入来源（2026-10-17；版本号登记自上游 release v2.13.0）
- 上游仓库：`https://github.com/NVIDIA/SkillSpector`（License **Apache-2.0**；**20,370 ★**；最后 push 2026-10-16）
- 来源文件：`skills/skill-inspector/SKILL.md`（main 分支；文件 blob sha `a968a585f321674581d691f8a5b030be211c926a`；该文件最后提交 `cfd4e64abe00fabbee84a7508787df5e95e7dad7`，2026-10-08）
- 装入时上游最新 release：**v2.13.0**（2026-10-16 发布）→ `metadata.version` 即登记此值；上游文件本身不写版本号。

## 本地适配（共 2 处，其余逐字节保上游）
- frontmatter 新增 `metadata: version: 2.13.0`——本库清单生成器（`gen_skill_manifest.py`）与体检器都要求版本可读。上游若自带版本，可删本条登记并改随上游值。
- frontmatter 补 `metadata: displayName: 装skill前安检（SkillSpector 官方审查件）`——发布仓（D:\Caoht-skills）README「中文名」列逐字取自它，缺字段属 `library_check.py` 硬红项；同为本地适配，上游变更不涉及。

## 入库前安检（按 skillhub-install-audit §二两把尺 ＋ 该支手册自带判据）
- 文件清单：仅 `SKILL.md`（7,559 字节）＋本文件。无脚本、无配置、无依赖文件、无 MCP 清单、无外联接口。
- 尺 1 · 危险模式扫（requests/urllib/httpx/subprocess/os.system/eval/exec/shutil/curl/socket…）：命中 **2 行**，全部是它自己审查判据里的词条（第 78 行"Execution risk: Does it use shell commands, subprocesses…"、第 81 行"Trigger risk…"），不是可执行代码。
- 尺 2 · 凭据/注入形状扫（api_key/sk-/environ/credential/ignore previous/base64/exfiltrat…）：命中 **4 行**（第 68/76/91/169 行），全部是审查判据清单词汇。
- 隐藏指令扫（零宽 U+200B/200C/200D、BOM U+FEFF）：**0 命中**。
- 运行命令面：唯一的"命令"是瞭望用只读检查（`find`/`rg`/`sed`/`jq`/`file`/`git diff`）与停留在用户请求面里的 `skillspector scan` 调用；它明文禁止静默装依赖、禁止跑目标 skill 的脚本、LLM 通道先询问。
- 结论：纯提示词 skill、无代码无外联无凭据 → 判 **APPROVE**（对应它自家 rubric 与 skillhub-install-audit P2 档），**直接入库**。

## 运行时备注
- 本机未装 `skillspector` CLI → 调度本支时按其手册自动转 **Manual Fallback**（语义审查人工走查），结论照出，只是置信度降低并须声明"没有跑静态扫描"。
