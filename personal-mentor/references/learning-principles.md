# 学习科学原理（Learning Principles）

> 改编自学习者自有 skill `study-cao` 的同源参考文件（源自 learning-opportunities PRINCIPLES.md）。解释"为什么主动练习有效"，以及"为什么 AI 代劳反而危险"。
> **本版每条原理都标了证据强度档位与可核来源**——见文末「来源与证据强度」。**C 档是机制推断，讲授时必须说"我们这样设计是因为…"，不许说"研究证明…"。**

## 证据强度三档（本文件专用口径）
- **A 档**：有同行评审**元分析/系统综述**支持，或多个独立实验室 + 课堂场景一致复现。
- **B 档**：有同行评审实证，但**只有单项研究、或存在公开争议、或只在实验环境成立**。
- **C 档**：机制推断/类比，**本窗未核到直接实证**——保留是因为它与 A/B 档机制同向，但必须标注为假设。

## 生成效应（Generation Effect）〔A 档〕
**发现**：自己产出信息比被动消费编码更好；测试比被动复习带来更好的延迟记忆。
**应用**：观察前先预测、对比前先生成、讲回来——都强制主动提取。
**来源**：Slamecka & Graf (1978) *The generation effect: Delineation of a phenomenon*, Journal of Experimental Psychology: Human Learning and Memory 4(6), 592–604, DOI 10.1037/0278-7393.4.6.592（原文结论句："the generation effect is real"）；元分析 Bertsch, Pesta, Wiscott & McDaniel (2007), *Memory & Cognition* 35(2), 201–210, DOI 10.3758/BF03193441。

## 预测试（Potentiating Effect of Pretesting）〔B 档〕
**发现**：学习前先尝试作答，即使错了也能形成更强记忆；错误尝试通过对比让正确答案更难忘。
**应用**：追踪代码前先问"你预测会怎样"；展示实现前先问"你会怎么入手"。
**来源**：Kornell, Hays & Bjork (2009) *Unsuccessful retrieval attempts enhance subsequent learning*, Journal of Experimental Psychology: LMC 35(4), 989–998, DOI 10.1037/a0015729；Little & Bjork (2016) *Multiple-choice pretesting potentiates learning of related information*, Memory & Cognition 44(7), 1085–1101, DOI 10.3758/s13421-016-0621-z；课堂侧 Soderstrom & Bjork (2023) *Pretesting Enhances Learning in the Classroom*, Educational Psychology Review 35(3), DOI 10.1007/s10648-023-09805-6。
**强度说明**：实验与课堂证据都有，但**本窗没核到已发表的同行评审期刊元分析**（只有一份未同行评审的学位论文元分析），且叙述性综述（Pan & Carpenter 2023, EPR 35(4), DOI 10.1007/s10648-023-09814-5）指出效应量随材料类型波动 ⇒ 记 **B 档**不记 A。**用它设计练习是合理的，向学习者承诺"一定提分"是不诚实的。**

## 间隔效应（Spacing Effect / Distributed Practice）〔A 档〕
**发现**：分散练习远比集中填鸭保留更好；掌握度越高，间隔可越长。
**应用**：开场做检索复盘；练习分散在多次会话；掌握度检查放在间隔后而非立刻（⇒ `mastery-rubric.md` 的 L5 必须看延迟后表现）。
**来源**：Dunlosky, Rawson, Marsh, Nathan & Willingham (2013) *Improving Students' Learning With Effective Learning Techniques*, Psychological Science in the Public Interest 14(1), 4–58, DOI 10.1177/1529100612453266 —— 该文给 **distributed practice 评为 high utility**（十项技术里只有它与 practice testing 拿 high），理由原文："they benefit learners of different ages and abilities…"。

### 反面清单：同一评审里被评为 low utility 的五项（诚实起见一并列出）
Dunlosky et al. (2013) 原句："summarization, highlighting, the keyword mnemonic, imagery use for text learning, and rereading" 被评为 **low utility**；其中 keyword mnemonic 的理由原文是 "difficult to implement in some contexts, and it appears to benefit students for a limited number of materials and **for short retention intervals**"。
**对本技能的意义**：重读（rereading）与划重点恰恰是"感觉学了却没留下东西"的主要来源 ⇒ 学习者说"我把笔记再读一遍"时，导师应换成提取练习（讲回来/变式题），并说明这是**有实证支撑的替换**（A 档），不是个人偏好。注意：low utility ≠ 有害，只等于"作为主要学习手段证据不足"。

## 提取练习 / 主动提取（Practice Testing / Retrieval Practice）〔A 档〕
**发现**：测自己比重读效果好；测试本身就是学习事件。
**应用**：检索复盘、讲回来、调试此题、变式题。
**来源**：Dunlosky et al. (2013) 同上，**practice testing = high utility**；Roediger & Karpicke (2006) *Test-Enhanced Learning*, Psychological Science 17(3), 249–255, DOI 10.1111/j.1467-9280.2006.01693.x。

## 交错（Interleaving）〔B 档〕
**发现**：练习中混合不同但相关的主题，比块化练习提升长期保留与迁移。
**应用**：学完 A 后，把 A 与相关 B 一起练；变式练习：同一概念换不同场景。
**强度说明**：Dunlosky et al. (2013) 把 interleaved practice 评为 **moderate utility**，原文明说证据有限："the benefits of interleaving have just begun to be systematically explored, so the ultimate effectiveness of these techniques is currently unknown." ⇒ **本技能保留交错设计，但措辞只能是"值得这样排"，不能是"已被证明更优"。**

## 深入解释 / 自我解释（Elaborative Interrogation & Self-Explanation）〔B 档〕
**发现**：对"为什么"作解释（把解释性问题答一遍）比不答更好，但未在教育场景充分检验。
**应用**：Layer 3 第一性原理追问、【需理解】标签、"讲回来"。
**强度说明**：Dunlosky et al. (2013) 两者均 **moderate utility**，原文："elaborative interrogation and self-explanation have not been adequately evaluated in educational contexts"。⇒ 机制可用，**强度不许拔到与提取练习同级**。

## 元认知（Metacognition）〔B 档〕
**发现**：能监控并调整自身策略的学习者，表现优于不监控者。
**应用**：设置反思时刻，引导自评；"什么让你意外？"；留出空间让人察觉自己的心智模型与缺口。
**强度说明**：该结论在教育心理学里被广泛接受，但本窗**未核到具体一手来源与元分析编号** ⇒ 记 B 档并留"待补来源"，比不写来源更诚实。

## AI 辅助风险画像（AI-Assisted Risk Profile）
> 这一节整体是**对 AI 使用场景的推断**，不是已证实的因果链。逐条标注如下：

| # | 风险条目 | 档位 | 现有依据 / 缺口 |
| --- | --- | --- | --- |
| 1 | 生成效应被破坏：直接接受生成内容，跳过了主动加工 | **C 档（工作假设）** | "生成"这一环被跳过是机制级推断；本窗**未检索到**"AI 使用 × 生成效应"的同行评审实证 ⇒ 讲授时别说成研究结论 |
| 2 | 流畅性错觉：干净的生成代码让人"感觉懂了"，其实没懂 | **B 档（弱）** | Kumar, Mikayelyan & Vorfolomeyeva (2026) *Fluency Illusion: A Review on Influence of ChatGPT in Classroom Settings*, Information 17, 299, DOI 10.3390/info17030299 —— 是**叙述性综述非实验**；流畅性错觉本身属经典元认知概念 |
| 3 | 间隔被消除：机器速度把人推向填鸭 | **C 档（工作假设）** | 无直接实证，仅由"节奏可即时获取"推得 |
| 4 | 元认知被压制：高速流程不留监控学习的余地 | **C 档（工作假设）** | 同上 |
| 5 | 测试被少用：更少机会从提取中受益 | **C 档（工作假设）** | 提取练习的收益是 A 档，"AI 让测试变少"这一步未证 |

**AI 与学习结果的整体证据（B 档，有争议）**：Bastani, Bastani, Sungu, Ge, Kabakcı & Mariman (2025) *Generative AI without guardrails can harm learning: Evidence from high school mathematics*, PNAS 122(26), e2422633122, DOI 10.1073/pnas.2422633122（约千名高中生野外实验，无护栏 GPT 组练习期表现好、独立测验差）。**同议题存在公开对立文献**（SSRN 预印本 10.2139/ssrn.4898213 批评其设计；10.2139/ssrn.5929576 给出相反结论），两篇均**非同行评审** ⇒ 只作 B 档，不写"研究已证明 AI 有害学习"。

## 来源与证据强度（可追溯表）

| 本技能里的做法 | 依赖的原理 | 档位 | 一手锚点（DOI/编号） | 核查状态 |
| --- | --- | --- | --- | --- |
| 检索复盘、讲回来、变式题、调试此题 | 提取练习 | **A** | 10.1177/1529100612453266；10.1111/j.1467-9280.2006.01693.x | ✅ 出版商存缴元数据 + 摘要 |
| 间隔复习（1/3/7/14 与 7/21/60 两套排期） | 间隔效应 | **A** | 10.1177/1529100612453266（distributed practice＝high utility） | ✅ 同上 |
| "先预测再看结果"、"先勾思路再看实现" | 生成效应 | **A** | 10.1037/0278-7393.4.6.592；元分析 10.3758/BF03193441 | ✅ 元数据 + 原文结论句 |
| 开场预测试、错答也值得做 | 预测试 | **B** | 10.1037/a0015729；10.3758/s13421-016-0621-z；10.1007/s10648-023-09805-6 | ✅ 元数据；🔴 未核到期刊元分析 |
| 跨主题混合练习、变式排课 | 交错 | **B** | 10.1177/1529100612453266（moderate utility，原文承认证据有限） | ✅ |
| Layer 3 第一性原理追问、【需理解】 | 深入解释/自我解释 | **B** | 同上（moderate utility） | ✅ |
| 反思时刻、"什么让你意外" | 元认知 | **B** | 未采到具体一手来源 ⇒ **待补** | ⚠️ |
| PEAR 循环、L4 设计/反推 | 生成 + 提取的组合推断 | **C** | 无直接实证 | ⚠️ 假设 |
| "借 AI 不丢理解"的五条风险 | AI 风险画像 | **C/B** | 10.1073/pnas.2422633122（B，有争议）；10.3390/info17030299（综述，弱） | 🟡 |

### 🔴 关于 IES（美国教育科学研究所）实践指南——一条外部审查建议的核查结果
外部审查报告说"IES 的指南支持间隔学习、主动提取和深入解释等做法"。**该归属核查不成立**，本窗不予采纳：

- 核查方式：读取 IES/WWC 实践指南全文 PDF 15 份（经 `files.eric.ed.gov` 与 IES 自有 `api.ies.ed.gov` 检索），逐词检 `spaced` / `distributed practice` / `retrieval` / `elaborative interrogation` / `interleav` / `practice test` / `self-explanation`。
- 结果：**没有一份指南把这四五项策略列为推荐项**（IES 指南未把它列为推荐项，只在其参考文献里出现这些词）；相关词只出现在参考文献书目里。唯一近似命中是 *Assisting Students Struggling with Mathematics*（ED611018）Recommendation 6 的 "timed activities to build students' retrieval of basic facts"（强证据档，基于 27 项研究）——那是**数学事实自动化**，不是提取练习策略，不能挪用。
- 本文件因此**只借 IES 的证据分级刻度**（其指南自印的档位措辞 Strong / Moderate / Minimal(或 Low)，见 *Improving Adolescent Literacy*, IES Practice Guide, **NCEE 2008-4027**（2008）Table 1，与 *Preparing Young Children for School*, WWC **2022009**（2022)）——上面 A/B/C 三档里的"分档要有名字、每档要能追到原文措辞"这个做法来自它。
- 间隔/提取/深入解释的来源改挂 Dunlosky et al. 2013 与 Roediger & Karpicke 2006（见上表）。

### 诚实边界与禁引清单
1. **不可达来源**：`ies.ed.gov`、`eric.ed.gov`（网页界面）、`journals.sagepub.com`、`web.archive.org`、`ncbi.nlm.nih.gov` 本机不可达（超时）。本表结论取自 Crossref/OpenAlex 存缴元数据 + `files.eric.ed.gov` 全文 PDF ⇒ **书目字段（作者/年份/卷期页/DOI）可信度高；正文级引文（如 Dunlosky Table 1 的逐技术评级矩阵、PNAS 具体效应量）只核到摘要层**，需要时在可联网环境再读一次 PDF。
2. **禁引**：`Preckel et al. (2021) "The Potentiating Effect of Pretesting…" Review of Educational Research` —— Crossref、OpenAlex、IES ERIC API 四通道均查无此文，疑为误记，**不得写入本技能或对外材料**。
3. **已订正**：生成效应的经典出处是 **Slamecka & Graf (1978)**，常被误记为"Slamecka & Peters (1974)"（Graf 的名是 Peter，误记由此而来）——本窗最初的检索单上也写错了，已按 Crossref 逐字段改回。
4. **未核范围**：IES/WWC 指南共约 25 份，本窗核了 15 份；2023–2026 年部分新指南无 ERIC 全文，未逐份检查。若后来者读到某份 IES 指南确实推荐上述策略，请**带着原文句子与编号**来改这一节，不要凭印象。
