# 许可证按「使用行为」判，不按「能不能下载」判

> 从 `SKILL.md` §5 拆出。主文只给三条判据与缺省挡位，这里放**逐字一手引文、条款号、可信度档位和防错引**。
> 取证日期：2026-10-12。可信度档位：**✅ 直读一手**（抓到许可证／官方文档原文逐字核对）｜🟡 待复核（两次独立抓取内容一致，未能对原始字节逐字复核）｜⚠️ 未采到一手来源（禁止当结论引用，更禁止用于打分）。

## 一、为什么「GPL／AGPL 一律不下源码」这种写法不成立

它把**五种完全不同的行为**压成一个动作。同一份 GPL 代码：只读、本地跑、改了自用、发给别人、改成网络服务——义务一条比一条重，而「下载」本身在这套许可证里几乎不是触发点。旧写法真正的来源是**作者自己要把成果公开发布**，所以提前把路堵死；那是口味，不是法律。口味保留在 `personal.local.md`，判据留在这里。

## 二、行为轴（先答"我要做哪件事"，再来查许可证族）

| # | 行为 | GPL-3.0 | AGPL-3.0 | 宽松族（MIT／Apache-2.0／BSD） | CC0／公有领域 | 含 NC 条款的 CC | 无 LICENSE 文件 |
|---|---|---|---|---|---|---|---|
| ① | 只读浏览、在线看、学习 | 许可证不约束 | 同 | 同 | 同 | 同 | 站内查看／fork 由 GitHub ToS §D.5 允许（✅），**不等于**可搬进工程 |
| ② | 本地跑起来用，不给任何人 | ✅ 明确允许（§2） | 同 | 允许 | 允许 | 非商用用途允许，商用需看条款 | 默认保留所有权利 ⇒ 无授权 |
| ③ | 改了自用，**不分发** | ✅ 无条件（§2 第二句） | 同（不分发就不触发 §13） | 允许 | 允许 | 同上 | 无授权 |
| ④ | **分发**（convey）源码或对象码，含写进你的可发布构建、随产品交付 | 整作品须按 GPL 提供（§5、§6） | 同 | 保留声明＋许可证副本即可 | 无条件 | 商用即违反 NC | 无授权 |
| ⑤ | 改好后**通过网络提供服务**，不发副本 | ⛔ **不触发**（§0： mere interaction 不算 conveying，✅） | 🔴 **触发**（§13：须给远程用户提供源码，✅） | 不触发 | 无义务 | 服务算不算商用需另判 | 无授权 |
| ⑥ | 抄一段进自己工程／**机械转译**成另一种语言 | 🟡 翻译＝修改（FAQ #TranslateCode）⇒ 按 ④ 处理 | 同 | 按各自条款（多数只需署名） | 可以 | 商用禁止 | 不可 |
| ⑦ | 与你的程序**链接**成一个程序后一起发 | 🟡 静态／动态都算组合作品（FAQ #GPLStaticVsDynamic）⇒ 按 ④ | 同 | LGPL 有专门通道（一手见 §三 #LGPLStaticVsDynamic） | 可以 | 需另判 | 不可 |

> 表里**宽松族那一列是本支的简化表述**（MIT 只需保留版权声明与许可副本；Apache-2.0 另需附 `NOTICE` 并说明修改；LGPL 的链接义务比 GPL 松），一手文本本窗未逐字复核 ⇒ 那一列按 **🟡 待复核** 计；引用时优先落到 §三 已带 ✅ 引文的那几族。CC0／公有领域、含 NC 条款的 CC 两列同理（CC 的官方条款文本不在本次取证范围内）。

## 三、逐字一手引文（条款号＋URL＋版本）
**GPL-3.0**（文本版本行逐字：`GNU GENERAL PUBLIC LICENSE / Version 3, 29 June 2007`；https://www.gnu.org/licenses/gpl-3.0.html ）`[✅ 直读一手]`

- §2 Basic Permissions：`"This License explicitly affirms your unlimited permission to run the unmodified Program."`
- §2（同一节，「改而不发」的依据）：`"You may make, run and propagate covered works that you do not convey, without conditions so long as your license otherwise remains in force."`
- §0 定义（**GPL 没有网络条款的一手反证就在这里**）：`"Mere interaction with a user through a computer network, with no transfer of a copy, is not conveying."`
- §5 Conveying Modified Source Versions 首句：`"You may convey a work based on the Program, or the modifications to produce it from the Program, in the form of source code under the terms of section 4, provided that you also meet all of these conditions:"`
- §5(c)：`"You must license the entire work, as a whole, under this License to anyone who comes into possession of a copy."`
- §6 Conveying Non-Source Forms 首句：`"You may convey a covered work in object code form under the terms of sections 4 and 5, provided that you also convey the machine-readable Corresponding Source under the terms of this License, in one of these ways:"`
- §6(b)：`"accompanied by a written offer, valid for at least three years..."`；§6(d)：对象码与「对应源码」同址等价提供。

**AGPL-3.0**（版本行：`Version 3, 19 November 2007`；https://www.gnu.org/licenses/agpl-3.0.html ）`[✅ 直读一手]`

- §13 标题逐字：`"13. Remote Network Interaction; Use with the GNU General Public License."`
- §13 正文（截取）：`"if you modify the Program, your modified version must prominently offer all users interacting with it remotely through a computer network (if your version supports such interaction) an opportunity to receive the Corresponding Source of your version"`
- Preamble 佐证：`"It requires the operator of a network server to provide the source code of the modified version running there to the users of that server."`

**GNU GPL FAQ**（https://www.gnu.org/licenses/gpl-faq.html ，页面无日期标注）`[🟡 待复核：两次独立抓取内容一致，未能对原始字节逐字复核]`

- #TranslateCode：`"Under copyright law, translation of a work is considered a kind of modification. Therefore, what the GPL says about modified versions applies also to translated versions."`
- #GPLStaticVsDynamic：`"No. Linking a GPL covered work statically or dynamically with other modules is making a combined work based on the GPL covered work."`
- #LGPLStaticVsDynamic：`"(1) If you statically link against an LGPLed library, you must also provide your application in an object (not necessarily source) format..."`
- #MereAggregation：`"An \"aggregate\" consists of a number of separate programs, distributed together on the same CD-ROM or other media."` ＋ `"The GPL permits you to create and distribute an aggregate, even when the licenses of the other software are nonfree or GPL-incompatible."`（同媒介并排放≠合成一个程序，这是「仓库里放个 reference_repos/ 目录」与「把它的代码 import 进来」的法律分界）
- #GPLOutput：`"The output of a program is not, in general, covered by the copyright on the code of the program."`（注意它讲的是**程序输出**，不是"读了它的实现后自己写一遍"）

**GitHub 官方文档**（页面无日期标注）`[✅ 直读一手]`

- Licensing a repository：`"However, without a license, the default copyright laws apply, meaning that you retain all rights to your source code and no one may reproduce, distribute, or create derivative works from your work."`
  https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/licensing-a-repository
- GitHub Terms of Service §D.5 License Grant to Other Users（页首 **Effective date: April 27, 2026**）：`"By making a repository public, you grant other Users a nonexclusive, worldwide license to use, display, perform and reproduce (by forking) Your Content through the Service as permitted by GitHub's functionality."`
  https://docs.github.com/en/site-policy/github-terms/github-terms-of-service
  ⇒ 读这句要读全：授权范围是**通过本站功能**（浏览、fork），不是「可以拿去用」。

## 四、⚠️ 未采到一手来源的一条（如实标注，禁当结论用）

「只学它的方法论／设计理念、不抄表达，是否完全不受 copyleft 约束」——**本支没有采到可直接引用的条文**。FAQ 里没有针对"读设计后自行实现"的专条（全文检索 idea／methodology／practice 无对应小节），最接近的 #GPLOutput 讲的是程序输出，射程不同。要把它写成断言，需要「思想／表达二分法」的一手锚（例如美国版权法 17 U.S.C. §102(b) 或等价权威解释），而本支**没有复核过那一手**。

因此本支的动作是：**把"这样算不算只学理念"当成一条必须写进报告的提问**，由人来裁决；🔴 禁止用这句话给「抄码／机械转译」开脱——那一条有 ✅／🟡 一手锚（翻译＝修改，见 §二 ⑥）。

## 五、缺省动作挡位（原六行矩阵的去处）

下表保留下来，但身份变了：**它是"不想逐案判定时的保守缺省挡位"，不是许可证的结论**。要放宽，就按 §二 的行为轴写出你依据哪一格；要收紧，写进 `personal.local.md`（那是口味，别混进通用判据）。

| 观察到 | 保守缺省动作 | 想放宽时的依据 |
|---|---|---|
| MIT／Apache-2.0／BSD／CC0／公有领域 | 可下载、可参考、可修改 | 宽松族只需保留声明与许可证副本 |
| GPL／LGPL／AGPL | 先只读 README／文档；**要下源码或抄码，必须先按 §二 定行为** | ②③（自用不分发）本就允许；⑤ 只有 AGPL 触发；⑥⑦ 一旦进了可发布构建就必须整作品开源 |
| API `licenseInfo` 为 null | **先查根目录** `LICENSE`／`COPYING`／`LICENSE.md` 再判，不得直接当「无 License」 | — |
| `spdx_id` 为 `NOASSERTION` | 打开 LICENSE 原文读：这值是「GitHub 认不出这份文件」，既不是「无许可证」也不是「随便用」 | — |
| 无任何 LICENSE 文件 | 只在线浏览记 URL；不下源码、不抄码 | ToS §D.5 只给浏览与 fork 权 |
| 含 NC（禁止商业性使用）条款 | 将来要商用／随产品交付的构建一律不得纳入 | NC 是条款本身的限制，非个人口味 |
| 作者标 Private／内部件／保密 | 零下载，只记公开设计理念 | — |
| 仓库体积超阈值（缺省 100 MB，REST `.size` 单位是 KB） | 点取关键文件，不 clone 全库 | 与许可证无关，纯取数成本 |

**两条同时命中时**（例：AGPL ＋ 2.5 GB）取更保守的那条做动作，并把宽松那条写进备注，不许静默挑舒服的。

## 六、防错引三条（本窗实测踩到的真坑）

1. 🔴 流传最广的那句 `"Activities other than copying, distribution and modification are not covered by this License; they are outside its scope. The act of running the Program is not restricted"` **是 GPLv2 的文本**（https://www.gnu.org/licenses/old-licenses/gpl-2.0.txt 第 72–76 行），**不能当 GPL-3.0 的引文**。v3 里承载同义的是 §0 的定义句与 §2 的两句（见 §三）。
2. 🔴 `"Remote Network Interaction"` 的条款号 **§13 属于 AGPL-3.0**；GPL-3.0 的 §13 讲的是「与 AGPL 的兼容」。说"GPL 有网络分发条款"就是把这两份文本串了线。
3. 🟡 「copyleft 会**传染**」是行话不是法律表述。准确说法：分发或组合成单一作品时，必须以同一许可证提供**整个作品**的对应源码（§5(c)、§6）。聚合（aggregate）不在此列（FAQ #MereAggregation）——这也是「`reference_repos/` 里放一份只读参考」与「把它 import 进你的构建」的分界。
