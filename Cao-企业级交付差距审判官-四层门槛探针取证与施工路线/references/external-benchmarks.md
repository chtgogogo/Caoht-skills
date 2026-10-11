# 外部判据锚与标准映射（联网取证用）

> **这是什么**：审判第三层/第四层时用来"横向对标"的外部尺子——标准、法规、认证、行业框架，每条带**版本号、发布机构、是否强制、适用范围、可证实性**。
> **主文（`../SKILL.md`）为什么不含这些**：主文是流程入口，行业与标准内容会过期、且分品类；把它们集中在这里，过期只改一处。
> **三条总纪律**：
> 1. 这些锚只定**严重度**和**该问什么**，不定"本项目有没有"——后者只能靠代码探针（军规 6）。
> 2. 引用任何一条，五要素必须随文带上（来源／版本或标准年号／发布日期／我方检索日期／适用范围），并给可信度档位。
> 3. **框架是提问清单，不是合规声明**。没读过某框架正文，就不许写"本项目符合它"；本包标 ⚠️ 的条目连"存在"都还没被一手来源证实。

**可信度图例（本包自 2026-10-12 起重扫一遍后的状态）**

| 标记 | 含义 |
|---|---|
| ✅ 本窗直读 | 2026-10-12 由本施工窗直接取到该来源页面（不是转述），可当一手判据 |
| 🟡 检索所得 | 由联网检索得到（含本窗派出的检索子窗），**本窗未逐字读原文** ⇒ 正式报告引用前要自己复核 |
| ⚠️ 待补一手 | 机构/版本号/日期没锁定，或查不到一手来源 ⇒ **只能当背景，不能当论据，更不能写"符合/不符合"** |

---

## 一、已核实的外部锚（可直接引用，仍需带五要素）

### 1.1 Web / API 应用安全 → OWASP ASVS

| 要素 | 值 |
|---|---|
| 名称 | OWASP Application Security Verification Standard（应用安全验证标准） |
| **现值版本** | **ASVS 5.0.0**，GitHub release `5.0.0` 发布 **2025-05-30**，文内标注 `date: May 2025` |
| 仍在用的旧线 | **4.0.3**（2021-10-28）。很多团队/扫描工具仍按 4.0.3 出报告 ⇒ 引用条款号必须先说哪一版 |
| 🔴 不可引用 | GitHub 上标 `latest` 的 **"Bleeding Edge" release（2026-09-03 自动生成）不是版本号**，别当"最新版" |
| 发布方 | OWASP（基金会，非盈利、供应商中立） |
| 是否强制 | **自愿**。ASVS 原文："rather than the ASVS prescriptively stating what level an application should be at, an organization should analyze its risks and decide…" |
| 认证声明 | OWASP 明确不认证：**"OWASP, as a vendor-neutral nonprofit, does not certify any vendors, verifiers, or software. Any assurance, trust mark, or certification claiming ASVS compliance is not officially endorsed by OWASP."** ⇒ 报告里写"通过 ASVS"必须写成"**自称满足 ASVS X.Y 若干条**"，不能写成"取得 ASVS 认证" |
| 授权条款 | CC BY-SA 4.0 |
| 一手 URL | https://github.com/OWASP/ASVS ｜ https://github.com/OWASP/ASVS/releases ｜ https://owasp.org/www-project-application-security-verification-standard/ |
| 可证实性 | ✅ 版本与发布日期＝本窗直读 GitHub Releases API（2026-10-12）；原话摘录＝检索子窗逐字取自官方文档，本窗未复读全文 ⇒ 引原话时标 🟡 |

**结构（引用条款必须带版本）**

- **v4.0.3：V1–V14** —— V1 架构设计与威胁建模 / V2 认证 / V3 会话管理 / V4 访问控制 / V5 校验·清洗·编码 / V6 存储密码学 / V7 错误处理与日志 / V8 数据保护 / V9 通信 / V10 恶意代码 / V11 业务逻辑 / V12 文件与资源 / V13 API 与 Web 服务 / V14 配置
- **v5.0.0：V1–V17（17 章，编号与需求号全部重构）** —— V1 编码与净化 / V2 校验与业务逻辑 / V3 Web 前端安全 / V4 API 与 Web 服务 / V5 文件处理 / V6 认证 / V7 会话管理 / V8 授权 / V9 自包含令牌 / V10 OAuth 与 OIDC / V11 密码学 / V12 安全通信 / V13 配置 / V14 数据保护 / V15 安全编码与架构 / V16 安全日志与错误处理 / V17 WebRTC
- 官方说明（v5）："in a major release … including requirement numbers"；"**The former V1 Architecture chapter has been removed.**"；从 4.0 迁移请读官方文档 `5.0/en/0x05-For-Users-Of-4.0.md`
- 章名对齐（v4→v5）：认证 V2→V6 ／ 会话 V3→V7 ／ 访问控制 V4→V8 ／ 校验与编码 V5→V1+V2 ／ 存储密码学 V6→V11 ／ 错误与日志 V7→V16 ／ 数据保护 V8→V14 ／ 通信 V9→V12 ／ 文件与资源 V12→V5 ／ API V13→V4 ／ 配置 V14→V13。**⚠️ 这张对齐表是本包按章名推的，不是官方对照表**，写正式报告前对着官方 0x05 文档核一遍。

**验证等级（Level 1/2/3，官方口径摘录 🟡）**

| 级 | 官方表述（v5） | 什么时候用它当企业级判据 |
|---|---|---|
| L1 | "the minimum requirements to consider when securing an application… around 20%" | 内部工具、演示规模、非敏感数据（第一层够用） |
| L2 | "Most applications should be striving to achieve this level… Around 50%" | 含敏感数据、多用户、对外服务（**第二/三层常见目标**） |
| L3 | "the goal for applications looking to demonstrate the highest levels of security… the final ~30%" | 金融、医疗、关键基础设施、强监管客户（第三层高要求） |

（v4.0.3 的口径："Level 1 … completely penetration testable / Level 2 … applications that contain sensitive data / Level 3 … the most critical applications"。）

**ASVS 不管什么（官方原话，防止把它当全流程标准 🟡）**

> "ASVS does not prescribe development lifecycle activities or dictate how the application should be built via a CI/CD pipeline; instead, it specifies the security outcomes that must be achieved within the product itself."
> "All other considerations, such as functional aspects, code style, or policy requirements, are out of scope."
> "The ASVS only contains requirements (must) and does not contain recommendations (should)."

**审判用法**

1. 先用主文 §4.1 竞品/法规线确认"客户或采购方要求到哪一级"（**等级由采购要求定，不由本包定**）。
2. 对照本项目时，**一条 ASVS 需求的满足只能是「已验证」**：要过主文 §2.4 三步（配置被加载／跑的是含该修复的版本／行为观察），只读到 `nginx.conf` 里写了安全响应头 ⇒ 记**未验证**。
3. 报告写法：`本项目自称满足 ASVS 5.0.0 V6 认证 若干条（来源：OWASP ASVS 5.0.0，2025-05-30；本窗检索日 2026-10-12；适用范围：Web/API 应用安全验证；可信度：一手标准文本）`。

### 1.2 AI 风险治理 → NIST AI RMF

| 要素 | 值 |
|---|---|
| 名称/编号 | **NIST AI Risk Management Framework 1.0**，报告编号 **NIST AI 100-1** |
| 发布 | **2023 年 1 月**（官方项目页记 2023-01-26）；**截至 2026-10-12 仍是现行版** |
| 🔴 现值提醒 | 官方 AIRC 站标注 "**Note: AI RMF 1.0 is being revised**"、"The Playbook will be revised after the AI RMF 1.1 is published" ⇒ **目前没有 1.1**，写"AI RMF 1.1"是编的 |
| 是否强制 | **自愿，两处官方原话**（🟡 逐字摘录自检索子窗）：AI RMF 1.0 §1 —— "The Framework is intended to be **voluntary**, rights-preserving, non-sector-specific, and use-case agnostic, providing flexibility to organizations of all sizes and in all sectors and throughout society…"；项目页 —— "intended for **voluntary use** and to improve the ability to incorporate trustworthiness considerations into the design, development, use, and evaluation of AI products, services, and systems." |
| 适用范围 | 不限行业、不限用例的**组织级风险管理框架**；它**不替代**法定义务（EU AI Act 等），也**不是**产品安全条款清单（那用 ASVS / OWASP GenAI 清单） |
| 四大功能（官方原话 🟡） | **GOVERN**："a cross-cutting function that is infused throughout AI risk management and enables the other functions" ／ **MAP**："establishes the context to frame risks related to an AI system" ／ **MEASURE**："employs quantitative, qualitative, or mixed-method tools … to analyze, assess, benchmark, and monitor AI risk and related impacts" ／ **MANAGE**："entails allocating risk resources to mapped and measured risks on a regular basis and as defined by the GOVERN function" |
| 七项可信特征（官方原话 🟡） | "valid and reliable, safe, secure and resilient, accountable and transparent, explainable and interpretable, privacy-enhanced, and fair with harmful bias managed"（有效可靠／安全／secure 与弹性／可问责与透明／可解释与可理解／隐私增强／公平且有害偏见受控） |
| 配套件 | **NIST AI 600-1《Generative AI Profile》2024-07-26**（DOI 10.6028/NIST.AI.600-1，🟡）；**AI RMF Playbook**（官方注明同样是 voluntary）；对抗性机器学习分类 **AI 100-2**（2025-03 版次，🟡） |
| 🔴 禁止引用 | **"NIST AI 500-2" 查不到一手来源**（不在 AIRC 官方 AI 出版清单里，nvlpubs 无记录）。凡见到引它的判据，按"来源不成立"处理 |
| 一手 URL | https://www.nist.gov/itl/ai-risk-management-framework ｜ https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.100-1.pdf ｜ https://airc.nist.gov/technical-reports/ ｜ Playbook: https://airc.nist.gov/AI_RMF_Knowledge_Base/Playbook |

**审判用法（当作第三/四层的组织判据）**

| 功能 | 审判要问的问题 | 落到哪一态 |
|---|---|---|
| GOVERN | 谁对 AI 输出负责？有书面职责、审批与变更控制吗？ | 常在**文档与组织**里，代码搜不到 ⇒ 未找到 ≠ 没有，要问客户/项目方要书面材料 |
| MAP | 这个 AI 功能用在什么场景、影响谁、失败后果多大，有记录吗？ | 报告 §六 场景判别 |
| MEASURE | 有没有评测集、指标、上线前后对比、监控？ | 回代码 + 运行观察（§2.4） |
| MANAGE | 测出来的风险谁来处置、怎么降级、多久复盘一次？ | 运维组织（🏛️/🤝 标签） |

> 引用句式：`本项目 AI 治理判据参照 NIST AI RMF 1.0（NIST AI 100-1，2023-01；自愿性框架，检索日 2026-10-12）GOVERN/MEASURE 两功能；该框架不构成法定合规结论。`

### 1.3 LLM / Agentic 应用的产品内风险 → OWASP GenAI Security Project

| 项 | 现值 | 可证实性 |
|---|---|---|
| **OWASP GenAI LLM Top 10** | "**LLM Top 10 for 2026**"，发布 **2026-08-04**；一手仓库 `GenAI-Security-Project/GenAI-LLM-Top10`（2026/final） | 🟡（旧的 `OWASP/www-project-top-10-for-large-language-model-applications` 页面已自称 "historical archive"，tag 只到 2024；**引用时写年份不写 v2.0/v1.1**，那是 2023 旧版号） |
| **Top 10 for Agentic Applications**（ASI01–ASI10，如 ASI06 Memory & Context Poisoning） | 项目确实存在（OWASP GenAI 官方首页点名） | ⚠️ **准确版本号与发布日期未取得一手页面**（子站被 Cloudflare 拦；二手多源称 2025-12 发"2026"版）⇒ 写"OWASP Agentic Top 10（版次日期待补一手）" |
| **Agent Control Standard (ACS)** | https://genai.owasp.org/resource/agent-control-standard-acs/ | ⚠️ 版次未取 |
| AI Testing Guide ／ AISVS（LLM SVS） | OWASP 项目页列出 | ⚠️ 版次未取 |
| URL | https://genai.owasp.org/ ｜ https://github.com/GenAI-Security-Project | — |

**分工**：ASVS 管**通用 Web/API 应用安全**基线；GenAI 清单管**模型、上下文、代理**特有风险（提示注入、上下文污染、工具滥用、记忆投毒）。两者都要对照，不能只引一个。

### 1.4 认证与法定要求现值（第三层常见阻塞）

| 要求 | 现值与年号 | 强制吗 | 适用范围 | 一手出处 / 可证实性 |
|---|---|---|---|---|
| **等保 2.0（中国大陆）** | 核心标准 **GB/T 22239-2019**《信息安全技术 网络安全等级保护基本要求》（发布 2019-05-10，实施 2019-12-01，状态现行）；定级配套 **GB/T 22240-2020**《…定级指南》；**级别＝五级** | **法定**（网络安全法要求）；定几级看系统受侵害客体与程度 | 在中国境内运营的网络系统 | 标准目录一手：https://openstd.samr.gov.cn/bzgk/gb/std_list?p.p2=22239 ✅（目录级直读）；"五级"正文出自转载件 ⚠️ B 级 |
| **ISO/IEC 27001** | **27001:2022**（2022-10-25 发布，现行），含 **Amd 1:2024**（气候行动，2024-02-23）；**27001:2013 已废止** | 自愿认证，但常被写进采购门槛 | 组织信息安全管理体系 | ⚠️ 本窗未直读 ISO 官方页（🟡）；另"IAF 转版截止 2025-10-31""中国等同采标为 GB/T 22080-2025"只见认证机构通知 ⇒ **标待核，不进报告** |
| **SOC 2** | **Type 1**＝某时点系统描述公允性 + 控制**设计**适当性；**Type 2**＝另加一段期间内的**运行有效性**测试（常见 3–12 个月） | 自愿；北美客户常作采购门槛 | 服务组织信任服务准则 | ⚠️ **未取得 AICPA 官方原句**（官网 SOC 2 页无定义句），一手应引 AICPA AT-C §205 / TSP §100（在许可墙内）。🔴 另注：**SOC 2 是 attestation 鉴证业务，不是 certification 认证**——报告里别写成"取得 SOC 2 认证" |
| **EU AI Act** | **Regulation (EU) 2024/1689**（2024-06-13 通过，OJ 2024-07-12，**2024-08-01 生效**）。🔴 **编号不是 2024/1679** | **法定**（对落入其范围的提供者/部署者） | 欧盟市场／影响欧盟用户 | ✅ 见下表 |

**EU AI Act 适用时间表（🔴 本 skill 旧版这张表已过期，2026-10-12 重扫）**

原时间表被 **Regulation (EU) 2026/1744**（"Digital Omnibus on AI"，2026-07-08 通过，OJ 2026-07-24，**2026-07-27 生效**）改写。下表读数取自欧委会官方政策页（**页面最后更新 2026-08-03**，本窗直读）：

| 义务 | 原日期 | **现值** | 状态 |
|---|---|---|---|
| 禁止性条款（Art. 5） | 2025-02-02 | **2025-02-02** | 未推迟 |
| GPAI（通用 AI）义务 | 2025-08-02 | **2025-08-02** | 未推迟 |
| **Annex III 高风险要求** | 2026-08-02 | **2027-12-02** | 🔴 **已推迟** |
| Annex I 嵌入式高风险 | 2027-08-02 | **2028-08-02** | 🔴 已推迟 |
| Art. 50 透明度义务 | — | 2026-08-02 | ⚠️ 该日期只见律所/合规类 B 级来源一致表述，欧委会页摘录未逐条列出 ⇒ **标待欧委会原文复核** |

修正案序言(1)原话（🟡）："Regulation (EU) 2024/1689 entered into force on 1 August 2024. The entry into application of its provisions is staggered, with all rules entering into application by 2 August 2027."
URL：https://eur-lex.europa.eu/eli/reg/2024/1689/oj ｜ https://eur-lex.europa.eu/eli/reg/2026/1744/oj/eng ｜ https://digital-strategy.ec.europa.eu/en/policies/regulatory-framework-ai ｜ https://digital-strategy.ec.europa.eu/en/news/ai-omnibus-enters-force

> **审判用法**：本项目是否落入"高风险类别"要**按品类与用途判断**，不是默认。旧版本 skill 写的"2026-08-02 广泛执法要求生效"**已不成立**——引用法规日期时，本包这张表也是快照，每次审判按 §4.1 法规线重查。

---

## 二、未查得一手来源的框架锚（**降级为提问清单**）

> 🔴 2026-10-12 复核结果：下面「锚一/锚二/锚三」这套说法（含"八域 32 项控制"）在标准组织、政府机构、公司白皮书里**都检索不到一手出处**；GitHub 亦 0 命中。它们的原始素材来自 2026-10-08 那次联网检索的博客/白皮书类结果，**当时没留 URL**。
> **用法约束**：可以当"该问什么"的提问清单继续用（内容本身有价值），**禁止**在任何报告里写成"符合/不符合 PSF"、"行业最系统的框架"、"PSF 八域 32 控制"这类**标准声明**。需要同类一手锚时，改用 §1.2（NIST AI RMF）、§1.3（OWASP GenAI）、§1.1（ASVS），或这些可查的一手替代件：CSA《Agentic AI Autonomy Levels and Control Framework》v1.0（2026-01，cloudsecurityalliance.org）、Google SAIF、ISO/IEC 42001（AI 管理体系）——**这三条本窗也未直读原文，引用前各自复核**。

### 锚一 · "生产安全八域"提问表（⚠️ 框架归属未证实）

| 域 | 问什么 | 本项目对应探针 | 一手锚可换成 |
|---|---|---|---|
| 1 输入治理 | 进来的数据有没有校验/清洗/去重 | 入库前 schema 校验、脏数据拦截 | ASVS V1/V2（编码与净化／校验与业务逻辑） |
| 2 输出验证 | 出去的结果有没有把关（置信度/规则闸） | LLM 输出有无 policy gate、置信阈值 | OWASP GenAI 清单 |
| 3 数据保护 | 敏感数据、PII、加密、最小权限 | 权限模型、字段级脱敏、审计日志 | ASVS V14/V11 |
| 4 可观测 | 有没有 trace/指标/告警，能不能定位失败 | 指标端点、决策日志、trace 表 | NIST AI RMF MEASURE |
| 5 部署安全 | 发布流程、回滚、环境隔离 | 迁移器、回滚预案、staging | ASVS V13 配置；NIST AI RMF GOVERN |
| 6 人工监督 | 人在哪一环、能不能否决、审批队列 | 审批流、例外队列、override 日志 | NIST AI RMF GOVERN/MANAGE |
| 7 安全 | 认证、授权、限流、防注入 | auth 中间件、rate limit、RBAC | ASVS V6/V7/V8 |
| 8 供应商韧性 | 依赖的外部服务挂了怎么办 | 超时/重试/降级/熔断（**注意重试可能来自依赖库或网关，见主文 §2.2**） | OWASP GenAI + 主文 §2.2 六位 |

> 判读仍然成立：**八项全无 ⇒ 离第三层很远；有 4–5 项 ⇒ 部分就绪**。反差洞察也保留：多数个人/开源项目常在"人工监督"（因为有审批流）上意外地强，而在"可观测"和"韧性"上意外地弱。

### 锚二 · "七个生产就绪门"（⚠️ 归属 Datawhisper，未查得一手出处）

业务成果与所有权／权限与决策边界／数据与集成就绪／服务可靠性／保证与证据／运营模型／受控变更。
**最有用的一条警告信号**（保留）：**"只有构建团队能解释失败 = 尚未成为运营服务"**——项目无告警、无值班、失败只能靠开发者看日志 ⇒ 运营门未过。

### 锚三 · "五域就绪评估"（⚠️ 归属 Digital Applied，未查得一手出处）

集成就绪／评估基础设施／监控可观测／组织所有权／范围与数据就绪。对应三条实践：**AI 运维先于放量、评估框架先于扩展、窄范围先行**。

### 锚四 · 法规与标准时间线 → **已由 §1.4 取代**（旧表里的"2026-08-02 广泛执法"是错的现值）

### 锚五 · 行业命中率基线（⚠️ 全部为 2026-10-08 检索快照，**未留机构报告名与 URL**）

| 说法 | 数值 | 状态 |
|---|---|---|
| AI agent 以完整安全/IT 审批上线的比例 | 约 14% | ⚠️ 未采到出处 |
| 高管自信现有策略能防护 vs 真有可见性 | 82% vs 21% | ⚠️ 未采到出处 |
| 为加速 AI 而放松安全控制的组织 | 约 90% | ⚠️ 未采到出处 |
| 无法解释非人类身份特权行为的组织 | 约 80% | ⚠️ 未采到出处 |
| 称"与现有系统集成"是最大部署障碍 | 约 46% | ⚠️ 未采到出处 |
| McKinsey 2025：刚开始规模化 / 卡在试点 | 约 1/3 ／ 2/3 | ⚠️ 报告名与版次未留 |
| Gartner：缺 AI-ready 数据 / 因此被弃 / 40%+ agentic 项目 2027 前被取消 | 63%／60%／40%+ | ⚠️ 具体调研与发布日期未留 |

**用法（v1.2.0 起收紧）**：这些数字**只能作背景一句**，句式必须带降级标注——"据公开行业报道（2026-10-08 检索，未获机构报告全名与 URL，可信度：未采到）称约 X%…"。**不得**用于严重度打分（风险三因子要按主文 §5 Step 7 用本项目自身证据取值），更不得写进对外汇报数字。财务/AP 类的行业数字登记在 `references/industry-ap-finance.md` §二。

---

## 三、复核与登记规则（怎么写进报告才不算违规）

1. **表头六列**（与行业包一致）：`事实主张 | 数值或条款 | 来源（机构+文档名+版本/年号） | 发布日期 / 我方检索日期 | 适用范围（辖区·哪类企业·是否强制） | 可信度（一手/二手/未采到）`。
2. **标准一律带版本号**：`ASVS 5.0.0`、`NIST AI 100-1（AI RMF 1.0）`、`GB/T 22239-2019`、`ISO/IEC 27001:2022`、`Regulation (EU) 2024/1689`。**只写标准简称不写版本 ⇒ 报告复检不合格**（主文 §5 Step 8）。
3. **区分"自愿框架"与"法定义务"**：ASVS／NIST AI RMF／SOC 2／ISO 27001 = 自愿（可被写进采购要求）；等保／EU AI Act = 法定。混写会把"没做自愿认证"误报成"违法"。
4. **框架符合性只能由证据支撑**：声称满足某框架某条 ⇒ 必须落到该条的主文 §2 三态证据；只有配置/文档在场 ⇒ 记未验证。
5. **本包不是当前性保证**：所有条目都是 2026-10-12 的重扫快照。每次审判按 §4.1 三条线重跑检索，并把实际检索词写进报告 §十一。

---

## 四、本包已知缺口（诚实清单）

1. **PSF/"八域 32 控制"、七门、五域**：查不到一手来源（2026-10-12 复核），已降级为提问清单；若将来找到真实一手出处，要在本包升级标记并写清发布方。
2. **锚五全部行业百分比**：无机构报告全名与 URL ⇒ `未采到`，不得用于打分。
3. **待补一手**：OWASP Agentic Top 10 版次与日期、AICPA SOC 2 官方定义原句、EU AI Act Art. 50 适用日期、ISO/IAF 转版截止与 GB/T 22080 采标状态、CSA/SAIF/ISO 42001 三件替代锚的原文。
4. **"本窗直读"的边界**：ASVS 版本与发布日期、EU 时间表读数＝本窗直接取到官方页面/API；但引号内的**英文原话摘录**来自检索子窗，本窗未逐字复读原文（已标 🟡）。**章名 v4→v5 对齐表是本包按章名推的，不是官方对照表。**
5. 本包只覆盖**常见外部判据的形态**，不宣称完备。审任何品类的具体监管要求，仍以 §4.1 法规线的现场检索为准。
