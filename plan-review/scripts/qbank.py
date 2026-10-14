#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""qbank.py —— 审判问题库的「选题器 + 生成器 + 体检器 + 候选区闸门 + 报告守门」（零依赖，默认只读）。

一句话：以前每次审判都要把整库通读一遍、库只增不删地长、结论只有「全绿才过」——本脚本把
「读哪些题」「库现在到底有多少题」「报告结论合不合法」三件事变成从条目现扫算出来的确定性动作。

子命令（默认全部只读，只有 render --apply / add / promote 会写盘）：
  scan      解析条目成结构化记录（可 --json 机读）
  counts    打印现扫容量总账（按状态／按类别／核心题数）
  select    按项目画像只挑「通用核心题 + 本任务相关题」，并打印加载范围与每条入选原因
  render    生成库内 AUTO 容量块与目录、SKILL.md 容量块、_题库索引.md、审判报告模板（默认干跑）
  check     结构体检：ID／闸门／四要素／生成物漂移／排除规则／分母闸
  add       新题进候选区（事件指纹去重 + 幂等，同指纹重复提交＝no-op）
  promote   候选 → 正式题库（先写验证过的适用范围，才分配 Q 号）
  gate      审一份填好的审判报告：结论三态、阻塞项解除条件、轮次上限、选题依据
  selftest  合成夹具自测（临时目录，绝不碰真实库），证明每条判据既能红也能绿

退出码：0＝通过　1＝有红项　2＝用法错误或「分母闸」失效（扫到 0 条时禁止打「无红项」——
        空集上的任何断言都是恒真的，等于没测）。
"""

import argparse
import contextlib
import datetime as _dt
import difflib
import fnmatch
import hashlib
import io
import json
import os
import re
import shutil
import sys
import tempfile

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ---------------------------------------------------------------- 受控词表
STATUS = ("active", "candidate", "retired")
CORE_YES, CORE_NO = "是", "否"
PROJ_TYPES = ("任意", "新建", "存量", "对外", "常驻", "一次性")
TRAITS = (
    "AI自动动作", "量化承诺", "度量统计", "对外发布", "外部建议", "并行施工",
    "外部依赖", "定时任务", "敏感数据", "真实用户", "人工环节", "存量迁移",
    "长期维护", "单一事实源", "大写入", "设计文档",
)
ALWAYS = "任意"          # 类型／特征轴上的「不设闸」取值
NO_TRAIT = "无"          # 候选题未定特征时的占位（正式题库不许用）
VERDICTS = ("可开工", "附条件开工", "暂缓")
HANDLES = ("未解除", "已解除", "外待", "残险")   # 阻塞项处置四值
MAX_ROUND = 3            # 复审轮次上限：第 3 轮仍堵 ⇒ 只能改判暂缓，不许开第 4 轮加题

BANK_REL = "references/审判问题库.md"
CAND_PUBLIC_REL = "references/候选区.md"
CAND_LOCAL_REL = "references/候选区.local.md"
INDEX_REL = "references/_题库索引.md"
TEMPLATE_REL = "assets/审判报告模板.md"
SKILL_REL = "SKILL.md"
# 「所在文件」列的截断锚点＝本支在磁盘上的目录名，运行时现取：权威库那头是中文长名，发布仓那头是
# plan-review（卡20 改名）。写死任何一端的字面量，另一端就会输出整条绝对路径——而且不报错。
SKILL_DIR_NAME = os.path.basename(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# 与 __tools/publish_sync.py 的 EXCLUDE_FILES 同形：本机私有候选区就是靠这个模式在发布侧被排除
PUBLISH_EXCLUDE = ("*.local.md", "*.pyc", "*.bak*")
# 本机私有候选题在生成物里的替代标签：生成物会被原样抄进公开仓，问题句不外发
LOCAL_MASK = "（本机私有候选题，问题句不外发——读 references/候选区.local.md）"

AUTO_BEG = "<!-- AUTO-BEGIN {tag}（scripts/qbank.py render --apply 生成，手改即被 check 判红） -->"
AUTO_END = "<!-- AUTO-END -->"

Q_RE = re.compile(r"^- \*\*(Q\d+)｜(.+?)\*\*[：:]?\s*(.*)$")
C_RE = re.compile(r"^- \*\*(C\d+)｜(.+?)\*\*[：:]?\s*(.*)$")
GATE_RE = re.compile(
    r"^  - 闸门：状态=([a-z]+)｜核心=([是否])｜类型=([^｜]+)｜特征=(.+?)\s*$")
CAT_RE = re.compile(r"^## ([A-Z]) ([^（(\n]+)")
NUM_IN_ID = re.compile(r"Q(\d+)$")
# 散文里的容量复述：数字 + 题／条题目／道题（AUTO 块之外出现即判红，写对了也红）
COUNT_RE = re.compile(r"(?<![\w])(\d{1,4})\s*(?:题|道题|条题目)")

ENTRY_RE = re.compile(r"^- \*\*([QC]\d+)｜")


def die(msg, code=2):
    print(msg)
    sys.exit(code)


class AppendArg(argparse.Action):
    """重复 --type/--trait 累加成列表（单值写法照旧可用）。"""

    def __call__(self, parser, ns, values, option_string=None):
        cur = getattr(ns, self.dest, None) or []
        setattr(ns, self.dest, cur + [values])


def vvals(val, default):
    """把 `A,B` / `--x A --x B` / None 三种写法统一成去重有序的取值列表。"""
    if val is None:
        val = []
    items = val if isinstance(val, (list, tuple)) else [val]
    out = []
    for it in items:
        for t in str(it).split(","):
            t = t.strip()
            if t and t not in out:
                out.append(t)
    return out or list(default)


def read_text(path):
    with io.open(str(path), "r", encoding="utf-8", newline="") as f:
        return f.read().replace("\r\n", "\n")


def write_text(path, text):
    """临时文件 + os.replace + 写后读回验收；不符返回 False（不落最终态）。"""
    parent = os.path.dirname(str(path))
    if parent and not os.path.isdir(parent):
        # 生成物可以落在还没建出来的目录上（比如随包新增的 assets/）——
        # 但只由这条通道建，且只建生成物自己的父目录，不猜别的
        os.makedirs(parent)
    tmp = str(path) + ".tmp-qbank"
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    if read_text(tmp) != text:
        os.remove(tmp)
        return False
    os.replace(tmp, str(path))
    return read_text(path) == text


# ---------------------------------------------------------------- 解析
class Item(object):
    def __init__(self, iid, title, lead, cat, cat_name, gate, lines, src):
        self.id = iid
        self.title = title
        self.lead = lead                      # 问号后的主张句
        self.cat = cat                        # 风险类别字母（类别即风险维度，不手写第二遍）
        self.cat_name = cat_name
        self.status = gate.get("状态", "")
        self.core = gate.get("核心", "")
        self.types = gate.get("类型", [])
        self.traits = gate.get("特征", [])
        self.lines = lines                    # 原文行（含闸门行），用于逐字回写
        self.src = src
        self.n = int(iid[1:]) if iid.startswith("Q") else None

    def text_without_gate(self):
        return [l for l in self.lines if not l.startswith("  - 闸门：")]

    @property
    def fingerprint(self):
        return norm_title(self.title)

    def __repr__(self):
        return "<%s %s>" % (self.id, self.title[:20])


def norm_title(s):
    """去重指纹用的规范化：去空白／标点／全角差异，只留汉字与字母数字。"""
    out = []
    for ch in s:
        if ch.isalnum() or "一" <= ch <= "鿿":
            out.append(ch.lower())
    return "".join(out)


def parse_gate(line):
    m = GATE_RE.match(line)
    if not m:
        return None
    st, core, types, traits = m.groups()
    return {
        "状态": st,
        "核心": core,
        "类型": [t.strip() for t in types.split(",") if t.strip()],
        "特征": [t.strip() for t in traits.split(",") if t.strip()],
    }


def parse_file(path, expect_status):
    """解析一个条目文件（正式题库或候选区）。返回 (items, cats, errors)。"""
    items, cats, errs = [], [], []
    if not os.path.isfile(str(path)):
        return items, cats, errs
    lines = read_text(path).split("\n")
    cat = cat_name = ""
    cur = None
    for ln in lines:
        cm = CAT_RE.match(ln)
        if cm:
            cats.append((cm.group(1), cm.group(2).strip()))
            cat, cat_name = cm.group(1), cm.group(2).strip()
            continue
        em = ENTRY_RE.match(ln)
        if em:
            qm = Q_RE.match(ln)
            cmm = C_RE.match(ln)
            m = qm or cmm
            if m is None:
                errs.append("%s：条目形状不合 `- **ID｜问题？**`（%s）" % (path, ln[:40]))
                cur = None
                continue
            if m is qm and expect_status != "bank":
                errs.append("%s：正式题库 ID `%s` 出现在候选区文件" % (path, m.group(1)))
            if m is cmm and expect_status != "candidate":
                errs.append("%s：候选号 `%s` 出现在正式题库文件" % (path, m.group(1)))
            cur = Item(m.group(1), m.group(2), m.group(3), cat, cat_name, {}, [ln], str(path))
            items.append(cur)
            continue
        if ln.startswith("  - 闸门：") and cur is not None:
            if len(cur.lines) > 1 and cur.lines[-1].startswith("  - 闸门："):
                errs.append("%s：%s 有第二条闸门行" % (path, cur.id))
            elif cur.lines[-1].startswith("  - "):
                errs.append("%s：%s 闸门行上面还有续行，闸门必须紧跟标题行" % (path, cur.id))
            g = parse_gate(ln)
            if g is None:
                errs.append("%s：%s 闸门行解析失败：%s" % (path, cur.id, ln))
            else:
                cur.status, cur.core, cur.types, cur.traits = (
                    g["状态"], g["核心"], g["类型"], g["特征"])
                cur.lines.append(ln)
            continue
        if cur is not None and ln.startswith("  "):
            cur.lines.append(ln)
            continue
        cur = None
    return items, cats, errs


def load_all(root, include_local=True):
    """读正式题库 + 候选区（本机私有那份不在场就明确降级，不报错）。"""
    bank_p = os.path.join(root, BANK_REL.replace("/", os.sep))
    cand_p = os.path.join(root, CAND_PUBLIC_REL.replace("/", os.sep))
    local_p = os.path.join(root, CAND_LOCAL_REL.replace("/", os.sep))
    bank, bank_cats, errs = parse_file(bank_p, "bank")
    cand, cand_cats, e2 = parse_file(cand_p, "candidate")
    local, local_cats, e3 = ([], [], [])
    local_present = os.path.isfile(str(local_p))
    if local_present and include_local:
        local, local_cats, e3 = parse_file(local_p, "candidate")
    errs.extend(e2)
    errs.extend(e3)
    return {
        "bank": bank, "cand": cand, "local": local,
        "cats": bank_cats, "cand_cats": cand_cats,
        "errs": errs, "local_present": local_present,
        "files": {BANK_REL: bank_p, CAND_PUBLIC_REL: cand_p, CAND_LOCAL_REL: local_p},
    }


# ---------------------------------------------------------------- select 判据
def applies(item, ptype, ptraits):
    """返回 (命中?, 原因)。核心题＝无条件；否则过类型闸与特征闸（两闸各自任一取值对上即算）。"""
    if item.core == CORE_YES:
        return True, "核心题（无画像也入选）"
    if item.status != "active":
        return False, ""
    why = []
    if ALWAYS in item.types:
        why.append("类型不设闸")
    elif ptype and ptype in item.types:
        why.append("类型命中:%s" % ptype)
    else:
        return False, ""
    if ALWAYS in item.traits:
        why.append("特征不设闸")
    else:
        hit = [t for t in item.traits if t in ptraits]
        if hit:
            why.append("特征命中:" + ",".join(hit))
        else:
            return False, ""
    return True, "；".join(why)


def select_items(items, ptype, ptraits, take_all):
    out = []
    for it in items:
        if it.status != "active":
            continue
        if take_all:
            out.append((it, "--all 全量载入（仅在扩容演练时用，日常审判请给画像）"))
            continue
        ok, why = applies(it, ptype, ptraits)
        if ok:
            out.append((it, why))
    return out


# ---------------------------------------------------------------- 生成物
def counts_block(store):
    bank = store["bank"]
    act = [i for i in bank if i.status == "active"]
    ret = [i for i in bank if i.status == "retired"]
    cand = store["cand"] + store["local"]
    core = [i for i in act if i.core == CORE_YES]
    cats = ordered_cats(bank)
    return [
        "> 现扫条目 %d（active %d｜candidate %d｜retired %d）｜核心题 %d｜风险类别 %d 类"
        % (len(bank), len(act), len(cand), len(ret), len(core), len(cats)),
        "> 闸门取值：状态 ∈ {%s}｜类型 ∈ {%s}｜特征 %d 个｜核心 ⟺「类型=任意 且 特征=任意」"
        % (", ".join(STATUS), ", ".join(PROJ_TYPES), len(TRAITS)),
        "> 本块与下列生成物由 `py scripts/qbank.py render --apply` 从条目现扫写入："
        "库内容量块与目录、SKILL.md 容量块、`%s`、`%s`。散文里再手写第二处数字＝`check` 判红（写对了也红）。"
        % (INDEX_REL, TEMPLATE_REL),
    ]


def dir_block(store):
    bank = [i for i in store["bank"] if i.status != "candidate"]
    parts = []
    for letter, name, ids in ordered_cats(bank):
        act = [i for i in bank if i.cat == letter and i.status == "active"]
        if not act:
            continue
        rng = "%s-%s" % (act[0].id, act[-1].id) if len(act) > 1 else act[0].id
        n_core = len([i for i in act if i.core == CORE_YES])
        parts.append("%s %s（%d 题／核心 %d：%s）" % (letter, name, len(act), n_core, rng))
    return ["· ".join(parts)]


def ordered_cats(items):
    seen, out = set(), []
    for it in items:
        if it.cat and it.cat not in seen:
            seen.add(it.cat)
            out.append((it.cat, it.cat_name))
    letter_names = {}
    for letter, name in out:
        letter_names[letter] = name
    return [(l, letter_names[l], None) for l in sorted(letter_names)]


def auto_wrap(tag, lines):
    return [AUTO_BEG.format(tag=tag)] + lines + [AUTO_END]


def render_text_of(text, tag, lines):
    """把 AUTO 块替换进文本；标记不在场就报错（不猜插入位置）。"""
    beg = AUTO_BEG.format(tag=tag)
    if beg not in text:
        raise RuntimeError("缺 AUTO 起始标记：%s（先把手工占位改成标记，再 render）" % beg)
    head, rest = text.split(beg, 1)
    if AUTO_END not in rest:
        raise RuntimeError("AUTO 块缺收尾标记 %s（%s）" % (AUTO_END, tag))
    _old, tail = rest.split(AUTO_END, 1)
    return head + beg + "\n" + "\n".join(lines) + "\n" + AUTO_END + tail


def build_index(store):
    bank = store["bank"]
    act = [i for i in bank if i.status == "active"]
    lines = [
        "# 题库索引（生成物，勿手改）",
        "",
        "> 本文件由 `py scripts/qbank.py render --apply` 从 `references/审判问题库.md` 与候选区**现扫**生成，"
        "手改会被 `check` 判红。要改内容请改条目本身的闸门行，再重跑 render。",
        "> 风险类别＝条目所在小节字母，不另写第二遍（防两处漂移）。**本文件不写生成时刻**——"
        "落了时间戳就永远与现扫「不一致」，体检每跑一次报一次漂移，等于把尺子调成常红；"
        "「什么时候生成的」由 git 与 CHANGELOG 回答。",
        "",
        "## 一、容量总账（现扫）",
        "",
        "| 指标 | 现扫值 |",
        "| --- | --- |",
        "| 正式题库条目 | %d |" % len(bank),
        "| active | %d |" % len([i for i in bank if i.status == "active"]),
        "| retired | %d |" % len([i for i in bank if i.status == "retired"]),
        "| candidate（公开候选区 + 本机候选区） | %d |" % (len(store["cand"]) + len(store["local"])),
        "| 核心题（= 通用最小集，无画像也入选） | %d |" % len([i for i in act if i.core == CORE_YES]),
        "| 风险类别 | %d |" % len(ordered_cats(bank)),
        "",
        "## 二、类别清单（自动生成）",
        "",
        "| 类别 | 名称 | 题数 | 核心题 | 题号 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for letter in sorted(set(i.cat for i in bank if i.cat)):
        rows = [i for i in bank if i.cat == letter and i.status != "candidate"]
        names = sorted(set(i.cat_name for i in rows))
        lines.append("| %s | %s | %d | %d | %s |" % (
            letter, "/".join(names), len(rows),
            len([i for i in rows if i.core == CORE_YES and i.status == "active"]),
            " ".join(i.id for i in rows)))
    lines += [
        "",
        "## 三、通用核心题子集（陌生项目开箱可用）",
        "",
        "| 题号 | 问题 | 风险类别 |",
        "| --- | --- | --- |",
    ]
    for i in [x for x in bank if x.core == CORE_YES and x.status == "active"]:
        lines.append("| %s | %s | %s %s |" % (i.id, i.title, i.cat, i.cat_name))
    lines += [
        "",
        "## 四、带闸门的题（按画像命中才加载）",
        "",
        "| 题号 | 风险类别 | 类型 | 特征 |",
        "| --- | --- | --- | --- |",
    ]
    for i in [x for x in bank if x.core != CORE_YES and x.status == "active"]:
        lines.append("| %s | %s %s | %s | %s |" % (
            i.id, i.cat, i.cat_name, ",".join(i.types), ",".join(i.traits)))
    lines += [
        "",
        "## 五、受控词表（闸门只能取这些值）",
        "",
        "- 状态：%s" % "／".join(STATUS),
        "- 项目类型：%s" % "／".join(PROJ_TYPES),
        "- 计划特征（%d 个）：%s" % (len(TRAITS), "／".join(TRAITS)),
        "- 核心：`是`／`否`；`核心=是` 必须且只能与「类型=任意 且 特征=任意」同时出现",
        "",
        "## 六、候选区在册（未验证，不进审判输出）",
        "",
    ]
    allcand = store["cand"] + store["local"]
    if not allcand:
        lines.append("（空）——`add` 一次即在此处出现一条；候选题不参与 `select`、不进报告模板。")
    else:
        lines.append("| 候选号 | 问题 | 所在文件 | 状态 |")
        lines.append("| --- | --- | --- | --- |")
        local_ids = set(c.id for c in store["local"])
        for c in allcand:
            # 🔴 本机私有候选区里的问题句不进生成物：本文件和模板一起被 publish_sync 原样抄进
            # 公开仓（`*.local.md` 只排除文件本身，排除不了它的内容），标题留在这儿＝外发泄漏
            shown = LOCAL_MASK if c.id in local_ids else c.title
            lines.append("| %s | %s | %s | %s |" % (
                c.id, shown, c.src.replace("\\", "/").split(SKILL_DIR_NAME + "/")[-1],
                c.status))
    lines += [
        "",
        "## 七、retired（保留编号、不进任何输出）",
        "",
    ]
    rets = [i for i in bank if i.status == "retired"]
    if not rets:
        lines.append("（空）——作废题把闸门改成 `状态=retired` 即退出输出，编号与原文留着，防历史引用失效。")
    else:
        for i in rets:
            lines.append("- %s｜%s（原文仍在库内，只退出输出）" % (i.id, i.title))
    lines.append("")
    return "\n".join(lines)


def build_template(store):
    act = [i for i in store["bank"] if i.status == "active"]
    lines = [
        "# 《XX计划》审判报告（第 1 轮）· 模板骨架",
        "",
        "> 本模板由 `py scripts/qbank.py render --apply` 从条目现扫生成（第 %s 节的类别行、"
        "结论与处置的合法取值都来自题库，`retired` 题不在此列）。复制一份填，不要直接改本文件。" % "二",
        "> 填完用 `py scripts/qbank.py gate <报告文件>` 过一遍机械判据，退出码 0 才算这份报告成立。",
        "",
        "## 〇、结论（三态取一，不许自造第四态）",
        "",
        "- 结论：可开工｜附条件开工｜暂缓（三取一，删掉其余两个）",
        "- 轮次：1",
        "- 一句话结论：能不能动工 + 最致命 ≤3 条",
        "- 重启条件：（结论=暂缓时必填，否则该行删掉）",
        "",
        "| 三态 | 成立条件 | 交付什么 |",
        "| --- | --- | --- |",
        "| 可开工 | 阻塞项 0 条，或全部 `处置=已解除` 且有解除证据 | 报告 + 存疑清单 |",
        "| 附条件开工 | 每条未解除阻塞项都走通合法出口（外待／残险），件齐且轮次 ≤ %d | 报告 + 条件清单 |" % MAX_ROUND,
        "| 暂缓 | 存在给不出解除条件的阻塞项，或轮次已超 %d | 报告 + 重启条件 |" % MAX_ROUND,
        "",
        "## 一、本轮画像与选题依据（缺节＝gate 判红）",
        "",
        "- 画像：类型=新建｜特征=AI自动动作,量化承诺（逐项写明，`无画像` 就写「通用」）",
        "- 加载范围：命中 N ／ 总数 %d（跳过 M 条）" % len(act),
        "- 选题命令：`py scripts/qbank.py select --type 新建 --trait AI自动动作 --trait 量化承诺`",
        "- 跳过项去向：（写了「本类与本次计划无关」即可，不必逐条给理由）",
        "",
        "## 二、逐类裁决表（类别行自动生成，一行一类）",
        "",
        "| 类别 | 名称 | 本类题号 | 裁决（全通过／有存疑／有中招） | 备注 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for letter in sorted(set(i.cat for i in act if i.cat)):
        rows = [i for i in act if i.cat == letter]
        lines.append("| %s | %s | %s | ⬜ | |" % (
            letter, rows[0].cat_name, " ".join(i.id for i in rows)))
    lines += [
        "",
        "## 三、阻塞项（每条一个小节，字段按处置类型必填）",
        "",
        "### B-1 阻塞项标题",
        "- 级别：致命｜重要｜细节（三取一）",
        "- 题号：Qxx",
        "- 证据：（引计划原文，不许转述）",
        "- 处置：未解除｜已解除｜外待｜残险（四取一）",
        "- 解除条件：（`处置≠未解除` 必填，须是可验证的一句话）",
        "- 责任方：",
        "- 时机／期限：",
        "- 验证方式：（看什么证据、由谁看）",
        "",
        "**两类合法出口的额外必填项**：",
        "",
        "- `处置=外待`（外部条件未满足）→ 再加两行：`- 等待对象：` 与 `- 超时后动作：`",
        "- `处置=残险`（可接受残余风险）→ 再加四行：`- 为什么可接受：`／`- 爆炸半径上限：`／"
        "`- 发现机制：`／`- 复核日期：YYYY-MM-DD`",
        "- `处置=已解除` → 再加一行：`- 解除证据：`（复审销账用）",
        "",
        "## 四、存疑清单（信息不足，不阻塞开工）",
        "",
        "| 题号 | 要问谁 | 要实测什么 |",
        "| --- | --- | --- |",
        "| Qxx |  |  |",
        "",
        "## 五、复审销账表（第 2 轮起填）",
        "",
        "| 上轮编号 | 处置 | 销账结论 |",
        "| --- | --- | --- |",
        "| B-1 | 外待 |  |",
        "",
        "## 六、沉淀（新题只能先进候选区，不许直接编 Q 号）",
        "",
        "- 沉淀：本轮无新题（或：`py scripts/qbank.py add --title …` 后写「本轮新增候选题 Cxx，已进候选区」）",
        "- 另库沉淀：（命中具体坑时走 pitfall-log，写在这里）",
        "",
        "## 七、落盘前自检（gate 会机械复查）",
        "",
        "- [ ] 结论取自三态之一，且与阻塞项处置一致",
        "- [ ] 每条未解除阻塞项走通了合法出口，或结论已改判暂缓",
        "- [ ] 画像与加载范围写了实际数字，没写「全部题都过了一遍」",
        "- [ ] 轮次 ≤ %d；第 %d 轮仍有未解除项 ⇒ 结论=暂缓 + 重启条件" % (MAX_ROUND, MAX_ROUND),
        "- [ ] 新增题号只出现在候选区，正式题库编号未变动",
        "",
        "> 现扫 active %d 条／retired %d 条／candidate %d 条（本模板不含生成时刻，理由见索引页眉）。"
        % (len(act), len(store["bank"]) - len(act),
           len(store["cand"]) + len(store["local"])),
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------- check
def check_store(store, root):
    red, warn = [], []
    bank = store["bank"]
    red.extend(store["errs"])
    if not bank:
        die("分母闸：现扫正式题库 0 条——空集上任何断言都是恒真的，判本尺子作废（%s）" % root, 2)
    # R1 稳定 ID：唯一、从 1 起连续
    seen = {}
    for it in bank:
        if it.id in seen:
            red.append("R1 ID 重复：%s（%s 与 %s）" % (it.id, seen[it.id], it.src))
        seen[it.id] = it.src
    nums = sorted(it.n for it in bank if it.n)
    if nums and nums != list(range(1, len(nums) + 1)):
        miss = [x for x in range(1, nums[-1] + 1) if x not in nums]
        red.append("R1 ID 缺口／跳号：缺 %s（新题取「当前最大号+1」，作废题保留编号）"
                   % ",".join(str(m) for m in miss[:8]))
    for it in bank:
        # R2 闸门取值合法 + 核心 ⟺ 无闸
        if it.status not in STATUS:
            red.append("R2 %s 状态非法：`%s`（只能 %s）" % (it.id, it.status, "/".join(STATUS)))
        if it.status == "candidate":
            red.append("R2 %s 状态=candidate 却躺在正式题库文件里（候选区才是它的家）" % it.id)
        if it.core not in (CORE_YES, CORE_NO):
            red.append("R2 %s 核心值非法：`%s`" % (it.id, it.core))
        for t in it.types:
            if t not in PROJ_TYPES:
                red.append("R2 %s 类型取值越界：`%s`（受控词表见索引第五节）" % (it.id, t))
        if ALWAYS in it.types and len(it.types) > 1:
            red.append("R2 %s 类型里 `任意` 与具体值混写（等于没闸，要么全写要么只留任意）" % it.id)
        for t in it.traits:
            if t not in TRAITS and t != ALWAYS:
                red.append("R2 %s 特征取值越界：`%s`" % (it.id, t))
        if NO_TRAIT in it.traits:
            red.append("R2 %s 特征=`无`：`无` 只许在候选区未定时用" % it.id)
        if ALWAYS in it.traits and len(it.traits) > 1:
            red.append("R2 %s 特征里 `任意` 与具体值混写" % it.id)
        no_gate = (it.core == CORE_YES)
        unconstrained = (it.types == [ALWAYS] and it.traits == [ALWAYS])
        if no_gate and not unconstrained:
            red.append("R2 %s 标了核心却带闸门（核心 ⟺ 类型=任意 且 特征=任意）" % it.id)
        if unconstrained and not no_gate:
            red.append("R2 %s 无任何闸门却不是核心题（那 select 永远全选，写 `核心=是`）" % it.id)
        # R3 四要素
        body = "\n".join(it.lines)
        if "▸ 爆：" not in body:
            red.append("R3 %s 缺「▸ 爆：」（何时爆）" % it.id)
        if "▸ 查：" not in body:
            red.append("R3 %s 缺「▸ 查：」（怎么查）" % it.id)
        if not it.lead.strip():
            red.append("R3 %s 缺「为何是坑」句（标题行问号后要有主张）" % it.id)
        # R6 类别归属
        if not it.cat:
            red.append("R6 %s 不在任何类别小节下（风险类别取自所在小节字母）" % it.id)
    core = [i for i in bank if i.core == CORE_YES and i.status == "active"]
    if not core:
        red.append("R8 核心题 0 条——没有「无画像也能跑」的最小集，选题制退化成全库穷举")
    # R5 候选／retired 排除面
    for c in store["cand"] + store["local"]:
        if c.status not in ("candidate",):
            red.append("R5 %s 在候选区却标着 `%s`（候选区只许 candidate）" % (c.id, c.status))
        if not c.title.strip():
            red.append("R3 %s 候选区条目缺问题句" % c.id)
        body = "\n".join(c.lines)
        for need, lab in (("▸ 爆：", "何时爆"), ("▸ 查：", "怎么查")):
            if need not in body:
                red.append("R3 %s 候选区条目缺「%s」（要素不齐不许进候选）" % (c.id, lab))
    bank_ids = set(i.id for i in bank)
    overlap = bank_ids & set((i.id for i in store["cand"] + store["local"]))
    if overlap:
        red.append("R5 同一编号既在正式题库又在候选区：%s" % " ".join(sorted(overlap)))
    seen_cid = {}
    for c in store["cand"] + store["local"]:
        if c.id in seen_cid and seen_cid[c.id] != c.src:
            red.append("R5b 候选号 %s 在两个候选区文件里各有一条（%s ↔ %s）——"
                       "promote 按号取条目会选错，跨两个文件必须唯一"
                       % (c.id, os.path.basename(seen_cid[c.id]), os.path.basename(c.src)))
        seen_cid[c.id] = c.src
    # R4 散文复述计数（AUTO 块之外）
    for rel in (BANK_REL, SKILL_REL, INDEX_REL, TEMPLATE_REL):
        p = os.path.join(root, rel.replace("/", os.sep))
        if not os.path.isfile(p):
            continue
        text = read_text(p)
        outside = strip_auto(text)
        for m in COUNT_RE.finditer(outside):
            if int(m.group(1)) < 2:
                continue
            line_no = outside[:m.start()].count("\n") + 1
            frag = outside[max(0, m.start() - 24):m.end() + 8].replace("\n", " ⏎ ").strip()
            red.append("R4 %s:%d 散文里复述了容量「%s」——数字只许活在 AUTO 块与索引里（现扫 %d），"
                       "写对了也红：第二处数字本身就是下一个漂移点"
                       % (rel, line_no, frag, len(bank)))
    # R7 生成物漂移
    drift = diff_generated(store, root)
    for d in drift:
        red.append("R7 %s" % d)
    # R9 随包文件在场（降级不报错，但要明说）
    for rel in (BANK_REL, CAND_PUBLIC_REL, INDEX_REL, TEMPLATE_REL):
        if not os.path.isfile(os.path.join(root, rel.replace("/", os.sep))):
            red.append("R9 随包文件缺：%s（render --apply 会生成，缺它＝生成物断链）" % rel)
    if not store["local_present"]:
        warn.append("R9 本机候选区 `候选区.local.md` 不在场（发布侧本来就该没有）——"
                    "个人项目特有候选题无处落，走通用候选区即可，不算缺陷")
    # R10 泄漏面：本机私有候选题的问题句不许出现在任何生成物里（生成物被 publish_sync 原样抄走，
    #       `*.local.md` 只挡得住文件本身，挡不住已经抄进索引的内容）
    for rel in (INDEX_REL, TEMPLATE_REL):
        p = os.path.join(root, rel.replace("/", os.sep))
        if not os.path.isfile(p):
            continue
        gtext = read_text(p)
        for c in store["local"]:
            if c.title and c.title in gtext:
                red.append("R10 本机私有候选题 %s 的问题句抄进了生成物 `%s`"
                           "——这份生成物会随包外发，个人项目内容跟着漏出去" % (c.id, rel))
    rets = [i for i in bank if i.status == "retired"]
    if rets:
        tpl_p = os.path.join(root, TEMPLATE_REL.replace("/", os.sep))
        if os.path.isfile(tpl_p):
            t = read_text(tpl_p)
            for i in rets:
                if re.search(r"\b%s\b(?!x)" % i.id, t):
                    red.append("R5 retired 题 %s 出现在生成的报告模板里（生成器漏了排除）" % i.id)
    return red, warn


def strip_auto(text):
    out, inblk = [], False
    for ln in text.split("\n"):
        if ln.startswith("<!-- AUTO-BEGIN"):
            inblk = True
            continue
        if ln.startswith("<!-- AUTO-END"):
            inblk = False
            continue
        if not inblk:
            out.append(ln)
    return "\n".join(out)


def diff_generated(store, root):
    """现扫应产出的生成物 vs 盘上现状；不一致就是漂移（render --apply 的干跑也走这里）。"""
    diffs = []
    try:
        plans = plan_writes(store, root)
    except RuntimeError as e:
        # AUTO 标记不在场是「还没施工」而不是「体检器崩了」——报成红项交给人读，别抛栈
        return ["R7 生成物断链：%s" % e]
    for path, should in plans:
        rel = os.path.relpath(path, root).replace("\\", "/")
        if not os.path.isfile(path):
            diffs.append("%s 不在场（需 render --apply）" % rel)
            continue
        if read_text(path) != should:
            diffs.append("%s 与现扫应产出逐字节不同（重跑 render --apply）" % rel)
    return diffs


def plan_writes(store, root):
    """返回 [(绝对路径, 应写入的完整文本)]。生成物内容只由条目决定，不含时钟。"""
    out = []
    bp = os.path.join(root, BANK_REL.replace("/", os.sep))
    if os.path.isfile(bp):
        t = read_text(bp)
        for tag, lines in (("容量块", counts_block(store)), ("目录", dir_block(store))):
            t = render_text_of(t, tag, lines)
        out.append((bp, t))
    sp = os.path.join(root, SKILL_REL.replace("/", os.sep))
    if os.path.isfile(sp):
        out.append((sp, render_text_of(read_text(sp), "容量块", counts_block(store))))
    out.append((os.path.join(root, INDEX_REL.replace("/", os.sep)), build_index(store)))
    out.append((os.path.join(root, TEMPLATE_REL.replace("/", os.sep)), build_template(store)))
    return out


# ---------------------------------------------------------------- gate（报告判据）
FIELD_RE = re.compile(r"^- ([^：:]+)[：:](.*)$")


def parse_report(text):
    """抽结论／轮次／画像／加载范围／阻塞项小节／存疑清单／沉淀行。"""
    rep = {"conclusion": "", "round": None, "profile": "", "loaded": None,
           "total": None, "blocks": [], "doubts": [], "deposit": "", "restart": "",
           "has_profile_sec": False}
    cur = None
    for ln in text.split("\n"):
        if ln.startswith("## ") and ("画像" in ln or "选题" in ln):
            rep["has_profile_sec"] = True
        m = re.match(r"^###\s+(B-\d+)\b", ln)
        if m:
            cur = {"id": m.group(1), "fields": {}}
            rep["blocks"].append(cur)
            continue
        if re.match(r"^###\s+S-\d+\b", ln):
            cur = None
            continue
        if ln.startswith("# ") or ln.startswith("## "):
            cur = None
        fm = FIELD_RE.match(ln.strip())
        if fm:
            key = fm.group(1).strip()
            val = fm.group(2).strip()
            if cur is not None:
                cur["fields"].setdefault(key, val)
                continue
            if key.startswith("结论"):
                rep["conclusion"] = val
            elif key.startswith("轮次"):
                mm = re.search(r"\d+", val)
                rep["round"] = int(mm.group()) if mm else None
            elif key.startswith("画像"):
                rep["profile"] = val
            elif key.startswith("加载范围"):
                mm = re.search(r"命中\s*(\d+)\s*／\s*总数\s*(\d+)", val)
                if mm:
                    rep["loaded"], rep["total"] = int(mm.group(1)), int(mm.group(2))
            elif key.startswith("重启条件"):
                rep["restart"] = val
            elif key.startswith("沉淀"):
                rep["deposit"] = val
        tm = re.match(r"^\|\s*(Q\d+)\s*\|([^|]*)\|([^|]*)\|", ln)
        if tm:
            rep["doubts"].append((tm.group(1), tm.group(2).strip(), tm.group(3).strip()))
    return rep


def gate_report(text, active_total, red, warn):
    rep = parse_report(text)
    if rep["conclusion"] not in VERDICTS:
        red.append("G1 结论缺失或不是三态之一：`%s`（只能 %s）"
                   % (rep["conclusion"], "/".join(VERDICTS)))
    if rep["round"] is None:
        red.append("G1 缺 `- 轮次：N`（轮次上限 %d 是终止判据，没有它「无限加题」无从拦截）" % MAX_ROUND)
    if not rep["has_profile_sec"] or not rep["profile"]:
        red.append("G1 缺「画像」行或选题依据节（没有选择依据＝回到全库遍历的起点）")
    if rep["loaded"] is None:
        red.append("G1 缺 `- 加载范围：命中 N ／ 总数 M`")
    elif rep["total"] and rep["total"] != active_total:
        warn.append("G9 报告写的总数 %d ≠ 现扫 active %d（报告是旧库填的，或有人手抄错了）"
                    % (rep["total"], active_total))
    elif rep["loaded"] == rep["total"] and "通用" not in rep["profile"]:
        warn.append("G9 命中数＝总数：本轮实际全量载入，确认不是回退到全库穷举")
    open_blocks = []
    for b in rep["blocks"]:
        f = b["fields"]
        hand = f.get("处置", "")
        if hand not in HANDLES:
            red.append("G2 %s 处置非法：`%s`（只能 %s）" % (b["id"], hand, "/".join(HANDLES)))
            continue
        if hand == "未解除":
            open_blocks.append(b)
            continue
        need = ["解除条件", "责任方", "时机／期限", "验证方式"]
        if hand == "外待":
            need += ["等待对象", "超时后动作"]
        if hand == "残险":
            need += ["为什么可接受", "爆炸半径上限", "发现机制", "复核日期"]
        if hand == "已解除":
            need += ["解除证据"]
        miss = [k for k in need if not f.get(k, "").strip() or set(f.get(k, "").strip()) <= set("⬜")]
        if miss:
            red.append("G2 %s 处置=%s 但缺必填件：%s（给不出这些件就不算合法出口）"
                       % (b["id"], hand, "、".join(miss)))
        for k in ("复核日期",):
            v = f.get(k, "")
            if v and not re.match(r"^\d{4}-\d{2}-\d{2}$", v.strip()):
                red.append("G2 %s 的 `%s`=%s 不是 YYYY-MM-DD（相对日期三个月后没人解得开）" % (b["id"], k, v))
    if rep["conclusion"] == "可开工" and rep["blocks"] and any(
            b["fields"].get("处置") != "已解除" for b in rep["blocks"]):
        red.append("G3 结论=可开工，但存在未全部解除的阻塞项（应判附条件开工或暂缓）")
    if rep["conclusion"] == "附条件开工":
        if open_blocks:
            red.append("G10 结论=附条件开工，但 %s 处置=未解除——「条件」只能是外待或残险两种出口，"
                       "既不给出口也不销账的阻塞项等于无限期等待，"
                       "要么补出口件，要么改判暂缓并写重启条件"
                       % "、".join(b["id"] for b in open_blocks))
        if rep["round"] and rep["round"] > MAX_ROUND:
            red.append("G4 第 %d 轮还想附条件开工——超过 %d 轮只能改判暂缓并写重启条件"
                       % (rep["round"], MAX_ROUND))
    if rep["conclusion"] == "暂缓" and not rep["restart"].strip():
        red.append("G5 结论=暂缓却没写「重启条件」（暂缓必须给出回来开工的门，否则只是搁置）")
    if rep["round"] and rep["round"] > MAX_ROUND and rep["conclusion"] != "暂缓":
        red.append("G7 轮次 %d > %d 而结论不是暂缓——终止判据被绕过" % (rep["round"], MAX_ROUND))
    for d, who, what in rep["doubts"]:
        if not who or not what or "⬜" in who + what:
            red.append("G6 存疑清单 %s 没写「要问谁／要实测什么」（存疑必须落成两个具体动作）" % d)
    if re.search(r"新增.*Q\d+", rep["deposit"]):
        red.append("G8 沉淀行里直接给了正式题库号 %s——新题必须先 `add` 进候选区，"
                   "由 promote 分配编号" % re.search(r"Q\d+", rep["deposit"]).group())
    return rep


# ---------------------------------------------------------------- add / promote
def next_cand_id(store):
    nums = [int(c.id[1:]) for c in (store["cand"] + store["local"]) if c.id.startswith("C")]
    return "C%d" % (max(nums) + 1) if nums else "C1"


def find_dup(store, title):
    fp = norm_title(title)
    exact = [i.id for i in (store["bank"] + store["cand"] + store["local"])
             if norm_title(i.title) == fp]
    if exact:
        return exact, "同指纹（问题句规范化后逐字相同）"
    hits = []
    for i in store["bank"]:
        b = norm_title(i.title)
        if len(b) < 6 or len(fp) < 6:
            continue
        ratio = difflib.SequenceMatcher(None, fp, b).ratio()
        if ratio >= 0.6:
            hits.append((ratio, i.id, i.title))
    hits.sort(reverse=True)
    if hits:
        return ["%s(近似 %.2f)" % (h[1], h[0]) for h in hits[:3]], "近似题（问题句高度相似，须人工裁决）"
    return [], ""


def cmd_add(root, store, args):
    title = args.title.strip("？? ")
    if not title:
        die("add：--title 不给空")
    for k, v in (("why", args.why), ("boom", args.boom), ("check", args.check)):
        if not (v or "").strip():
            die("add：缺 --%s（要素不齐不许进候选区：%s）" % (k, ", ".join(
                ["why 为何是坑", "boom 何时爆", "check 怎么查"])))
    traits = vvals(args.trait, [NO_TRAIT])
    types = vvals(args.type, [ALWAYS])
    for t in traits:
        if t not in TRAITS and t != NO_TRAIT:
            die("add：特征取值越界 `%s`（受控词表 %d 个：%s）" % (t, len(TRAITS), "/".join(TRAITS)))
    for t in types:
        if t not in PROJ_TYPES:
            die("add：类型取值越界 `%s`（%s）" % (t, "/".join(PROJ_TYPES)))
    dups, kind = find_dup(store, title)
    gate = "  - 闸门：状态=candidate｜核心=%s｜类型=%s｜特征=%s" % (
        CORE_NO, ",".join(types), ",".join(traits))
    target_rel = CAND_LOCAL_REL if args.personal else CAND_PUBLIC_REL
    path = store["files"][target_rel]
    if not os.path.isfile(path):
        die("add：候选区文件不在场 %s（发布侧没有本机私有候选区属正常，写通用候选区即可）" % path)
    cur = parse_file(path, "candidate")[0]
    same = [c for c in cur if norm_title(c.title) == norm_title(title)]
    if same:
        print("幂等：候选区已有同题 %s（%s），本次不写入" % (same[0].id, target_rel))
        return 0
    cid = next_cand_id(store)
    case = (" ▸ 案例：" + args.case.strip()) if (args.case or "").strip() else ""
    line = "- **%s｜%s？** %s ▸ 爆：%s ▸ 查：%s%s" % (
        cid, title, args.why.strip(), args.boom.strip(), args.check.strip(), case)
    if dups and not args.force:
        print("🔴 去重闸拦住：%s ⇒ %s" % (kind, "、".join(dups)))
        print("   确认不是重影再加 --force（写入后带「重影待裁决」注记）")
        return 1
    text = read_text(path)
    note = ""
    if dups:
        note = "\n  - 重影待裁决：与 %s 近似（%s），人工裁决前不许 promote" % ("、".join(dups), kind)
    add_block = "\n## 候选 %s（%s 登记）\n\n%s\n%s\n%s" % (
        cid, args.date or _dt.date.today().isoformat(), line, gate + note,
        "- 待验证：适用范围（谁在什么条件下需要这道题）——promote 前必须写清")
    new_text = text.rstrip("\n") + add_block + "\n"
    if args.apply:
        if not write_text(path, new_text):
            die("add：写后读回不符，已放弃落盘（%s）" % path)
        print("✅ 已写入候选区 %s：%s｜%s" % (target_rel, cid, title))
    else:
        print("干跑（加 --apply 才落盘）：将在 %s 追加：\n%s" % (target_rel, add_block.strip("\n")))
    return 0


def cmd_promote(root, store, args):
    matches = [c for c in store["cand"] + store["local"] if c.id == args.id]
    if not matches:
        die("promote：候选区没有 %s（现有：%s）" % (
            args.id, " ".join(sorted(c.id for c in store["cand"] + store["local"])) or "空"))
    if len(matches) > 1:
        die("promote：%s 在两个候选区文件里各有一条（%s）——按号取条目会选错，"
            "先给其中一条换号（候选号跨两个文件唯一）" % (
                args.id, "、".join(os.path.basename(m.src) for m in matches)))
    c = matches[0]
    if "重影待裁决" in "\n".join(c.lines):
        die("promote：%s 带「重影待裁决」注记，先人工裁决再去注记" % args.id)
    scope = (args.scope or "").strip()
    if not scope:
        die("promote：必须给 --scope「验证过的适用范围」（一句话：哪类计划需要它、为什么）——"
            "没验证适用性就进正式库＝未整理的膨胀")
    if c.status != "candidate":
        die("promote：%s 状态=%s，候选区只处理 candidate" % (args.id, c.status))
    cats = dict(store["cats"])
    if args.cat not in cats:
        die("promote：--cat 取值 %s 不在现扫类别里（现有：%s）" % (args.cat, " ".join(sorted(cats))))
    nums = [i.n for i in store["bank"] if i.n]
    new_id = "Q%d" % (max(nums) + 1 if nums else 1)
    types = vvals(args.type, c.types) or [ALWAYS]
    traits = vvals(args.trait, c.traits) or [ALWAYS]
    if NO_TRAIT in traits:
        die("promote：%s 的特征还是占位 `无`——用 --trait 给出验证结果（哪类计划才需要它），"
            "确认任何计划都要问就给 --trait 任意（那时才可标核心）" % args.id)
    core = CORE_YES if (types == [ALWAYS] and traits == [ALWAYS]) else CORE_NO
    if args.core == CORE_YES and core != CORE_YES:
        die("promote：--core 是 与闸门矛盾（核心 ⟺ 类型=任意 且 特征=任意，当前 类型=%s／特征=%s）"
            % (",".join(types), ",".join(traits)))
    new_gate = "  - 闸门：状态=active｜核心=%s｜类型=%s｜特征=%s" % (core, ",".join(types), ",".join(traits))
    new_line = re.sub(r"\b%s\b" % c.id, new_id, c.lines[0], count=1).rstrip()
    new_line += " ▸ 适用范围：%s" % scope
    bank_p = store["files"][BANK_REL]
    lines = read_text(bank_p).split("\n")
    head_i = None
    for idx, ln in enumerate(lines):
        m = CAT_RE.match(ln)
        if m and m.group(1) == args.cat:
            head_i = idx
            break
    if head_i is None:
        die("promote：正式题库找不到类别 %s 的小节标题" % args.cat)
    j = head_i + 1
    while j < len(lines) and not (ENTRY_RE.match(lines[j]) or lines[j].startswith("## ")
                                  or lines[j].startswith("<!-- AUTO")):
        j += 1
    k = j
    while k > head_i + 1 and not lines[k - 1].strip():
        k -= 1
    new_btext = "\n".join(lines[:k] + ["", new_line, new_gate] + lines[k:])
    cand_p = c.src
    removed = remove_candidate_block(read_text(cand_p), args.id)
    if not args.apply:
        print("干跑（加 --apply 才落盘）\n将写入正式题库（类别 %s）：\n%s\n%s\n将从 %s 移出候选 %s"
              % (args.cat, new_line, new_gate, os.path.basename(cand_p), args.id))
        return 0
    if not write_text(bank_p, new_btext):
        die("promote：正式题库写后读回不符，已放弃（候选区未动）")
    if not write_text(cand_p, removed):
        die("promote：候选区写后读回不符（正式题库已写入 %s，请人工核对后再重跑）" % new_id)
    st = load_all(root)
    for path, should in plan_writes(st, root):
        if not write_text(path, should):
            die("promote：生成物 %s 写入失败，请重跑 render --apply" % path)
    red = check_store(st, root)[0]
    print("✅ %s → %s（类别 %s｜核心=%s｜类型=%s｜特征=%s；候选区那条已移出；生成物已同步）" % (
        args.id, new_id, args.cat, core, ",".join(types), ",".join(traits)))
    if red:
        print("🔴 promote 后体检仍有红项 %d 条：%s" % (len(red), "｜".join(red[:3])))
        return 1
    return 0


def remove_candidate_block(text, cid):
    lines = text.split("\n")
    out, skip = [], False
    for ln in lines:
        if ln.startswith("## 候选 %s（" % cid):
            skip = True
            continue
        if skip and ln.startswith("## "):
            skip = False
        if not skip:
            out.append(ln)
    return "\n".join(out)


# ---------------------------------------------------------------- CLI
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0], prog="qbank.py")
    ap.add_argument("--root", default=None, help="技能根目录（默认取脚本上一层＝本 skill 目录，不静默用当前目录）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("scan")
    p.add_argument("--json", action="store_true")
    p.add_argument("--status", default="all")
    p = sub.add_parser("counts")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("select")
    p.add_argument("--type", dest="ptype", default=None, help="项目类型，取值见 --profile")
    p.add_argument("--trait", dest="traits", action="append", default=[])
    p.add_argument("--profile", default=None, help="通用（只核心题）；给了就忽略 --type/--trait")
    p.add_argument("--all", action="store_true", help="全量载入（仅扩容演练用，日常审判别用）")
    p.add_argument("--ids", action="store_true", help="只打印题号，便于贴进报告")
    p = sub.add_parser("render")
    p.add_argument("--apply", action="store_true")
    p = sub.add_parser("check")
    p.add_argument("--quiet", action="store_true")
    p = sub.add_parser("gate")
    p.add_argument("report")
    p = sub.add_parser("add")
    p.add_argument("--title", required=True)
    p.add_argument("--why", required=True)
    p.add_argument("--boom", required=True)
    p.add_argument("--check", dest="check", required=True)
    p.add_argument("--case", default="")
    p.add_argument("--source", default="")
    p.add_argument("--type", dest="type", default=None, action=AppendArg)
    p.add_argument("--trait", dest="trait", default=None, action=AppendArg)
    p.add_argument("--personal", action="store_true", help="个人项目特有题，写进本机候选区（同步器按 *.local.md 排除，不外发）")
    p.add_argument("--date", default="")
    p.add_argument("--force", action="store_true")
    p.add_argument("--apply", action="store_true")
    p = sub.add_parser("promote")
    p.add_argument("--id", required=True)
    p.add_argument("--cat", required=True)
    p.add_argument("--scope", required=True)
    p.add_argument("--type", dest="type", default=None, action=AppendArg,
                   help="验证后的项目类型（默认沿用候选区那条；可重复给多个）")
    p.add_argument("--trait", dest="trait", default=None, action=AppendArg,
                   help="验证后的特征（候选区留着占位 `无` 时必须给；可重复给多个）")
    p.add_argument("--core", default="否")
    p.add_argument("--apply", action="store_true")
    sub.add_parser("selftest")

    a = ap.parse_args(argv)
    root = a.root or str(Path_default())
    if not os.path.isdir(root):
        die("--root 不是目录：%s" % root)
    store = load_all(root)
    act = [i for i in store["bank"] if i.status == "active"]

    if a.cmd == "scan":
        rows = []
        for it in store["bank"] + store["cand"] + store["local"]:
            if a.status != "all" and it.status != a.status:
                continue
            rows.append({"id": it.id, "title": it.title, "cat": it.cat, "cat_name": it.cat_name,
                         "status": it.status, "core": it.core, "types": it.types,
                         "traits": it.traits, "src": os.path.relpath(it.src, root).replace("\\", "/")})
        if a.json:
            print(json.dumps(rows, ensure_ascii=False, indent=1))
        else:
            for r in rows:
                print("%s｜%s｜%s｜%s｜核心=%s｜类型=%s｜特征=%s｜%s" % (
                    r["id"], r["cat"], r["status"], r["title"], r["core"],
                    ",".join(r["types"]), ",".join(r["traits"]), r["src"]))
            print("—— 现扫条目 %d（正式 %d／候选 %d）" % (
                len(rows), len(store["bank"]), len(store["cand"]) + len(store["local"])))
        for e in store["errs"]:
            print("🔴 %s" % e)
        return 1 if store["errs"] else 0

    if a.cmd == "counts":
        d = {
            "条目": len(store["bank"]),
            "active": len([i for i in store["bank"] if i.status == "active"]),
            "retired": len([i for i in store["bank"] if i.status == "retired"]),
            "candidate": len(store["cand"]) + len(store["local"]),
            "核心题": len([i for i in act if i.core == CORE_YES]),
            "类别": len(ordered_cats(store["bank"])),
        }
        if a.json:
            print(json.dumps(d, ensure_ascii=False))
        else:
            for k, v in d.items():
                print("%s：%d" % (k, v))
        return 0

    if a.cmd == "select":
        ptype, ptr = a.ptype, set(a.traits)
        if a.profile == "通用":
            ptype, ptr = None, set()
        elif a.profile and a.profile != "通用":
            die("select：--profile 只认 `通用`（其余请给 --type/--trait）")
        if a.profile == "通用" and not a.all:
            picked = [(i, "核心题（无画像也入选）") for i in act if i.core == CORE_YES]
        else:
            picked = select_items(store["bank"], ptype, ptr, a.all)
        print("画像：%s" % ("通用（无画像＝只取核心题）" if a.profile == "通用" else
                      "类型=%s｜特征=%s" % (ptype or "未给", ",".join(sorted(ptr)) or "未给")))
        print("加载范围：命中 %d ／ 现扫 active 总数 %d（跳过 %d 条）" % (
            len(picked), len(act), len(act) - len(picked)))
        print("入选判据：核心题无条件入选；带闸门的题要过「类型闸」与「特征闸」（任一击中即入选）")
        by_cat = {}
        for it, why in picked:
            by_cat.setdefault((it.cat, it.cat_name), []).append((it, why))
        for key in sorted(by_cat):
            print("\n## %s %s（%d 条）" % (key[0], key[1], len(by_cat[key])))
            for it, why in by_cat[key]:
                if a.ids:
                    print("- [ ] %s｜%s" % (it.id, it.title))
                else:
                    print("- [ ] %s｜%s｜入选原因：%s" % (it.id, it.title, why))
        if a.ids:
            print("\n题号：%s" % " ".join(it.id for it, _ in picked))
        if not picked:
            print("🔴 命中 0 条——画像给错或核心题被清空，判本尺子作废")
            return 1
        return 0

    if a.cmd == "render":
        try:
            plans = plan_writes(store, root)
        except RuntimeError as e:
            die("render：%s" % e)
        wrote = 0
        for path, should in plans:
            rel = os.path.relpath(path, root).replace("\\", "/")
            cur = read_text(path) if os.path.isfile(path) else ""
            if cur == should:
                print("已一致：%s" % rel)
                continue
            diff = list(difflib.unified_diff(cur.split("\n"), should.split("\n"),
                                            lineterm="", n=1))
            print("将改：%s（%+d 行差异）" % (rel, max(0, len(diff) - 2)))
            if not a.apply:
                print("\n".join(diff[:40]))
                print("……（干跑，未写盘；--apply 才落盘）" if len(diff) > 40 else "")
                continue
            if not write_text(path, should):
                die("render：%s 写后读回不符，已放弃" % rel)
            wrote += 1
            print("✅ 已写：%s" % rel)
        if not a.apply:
            print("\n干跑完成：未改动任何文件。")
        return 0

    if a.cmd == "check":
        red, warn = check_store(store, root)
        if not a.quiet:
            print("现扫：%s" % counts_line(store))
            print("豁免登记（不是盲区）：CHANGELOG.md＝证词层（历史数字不回改）；"
                  "案例文件＝发源战报（数字是当年读数）；生成物本身＝由 render 写入")
            for w in warn:
                print(" 🟡 %s" % w)
            print("红项 %d 条／警告 %d 条" % (len(red), len(warn)))
            for r in red:
                print(" 🔴 %s" % r)
            if not red:
                print("✅ 无红项")
        else:
            print("红项 %d／警告 %d" % (len(red), len(warn)))
        return 1 if red else 0

    if a.cmd == "gate":
        p = a.report
        if not os.path.isfile(p):
            die("gate：报告文件不在场：%s" % p)
        red, warn = [], []
        gate_report(read_text(p), len(act), red, warn)
        print("被检报告：%s｜现扫 active %d" % (os.path.basename(p), len(act)))
        for w in warn:
            print(" 🟡 %s" % w)
        for r in red:
            print(" 🔴 %s" % r)
        print("红项 %d 条／警告 %d 条" % (len(red), len(warn)))
        if not red:
            print("✅ 报告判据通过（结论=%s）" % parse_report(read_text(p))["conclusion"])
        return 1 if red else 0

    if a.cmd == "add":
        return cmd_add(root, store, a)
    if a.cmd == "promote":
        return cmd_promote(root, store, a)
    if a.cmd == "selftest":
        return selftest()
    return 0


def counts_line(store):
    bank = store["bank"]
    return "条目 %d｜active %d｜retired %d｜candidate %d｜核心题 %d｜类别 %d" % (
        len(bank), len([i for i in bank if i.status == "active"]),
        len([i for i in bank if i.status == "retired"]),
        len(store["cand"]) + len(store["local"]),
        len([i for i in bank if i.core == CORE_YES and i.status == "active"]),
        len(ordered_cats(bank)))


def Path_default():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(here)


# ---------------------------------------------------------------- selftest
FIX_BANK = """# 审判问题库（测试夹具）

<!-- AUTO-BEGIN 容量块（scripts/qbank.py render --apply 生成，手改即被 check 判红） -->
<!-- AUTO-END -->

<!-- AUTO-BEGIN 目录（scripts/qbank.py render --apply 生成，手改即被 check 判红） -->
<!-- AUTO-END -->

## A 结构性矛盾
- **Q1｜铁则有无过度承诺？** 全称主张只对部分环节成立 ▸ 爆：例外出现时 ▸ 查：逐环节找反例
  - 闸门：状态=active｜核心=是｜类型=任意｜特征=任意
- **Q2｜只在新建时才需要问的题？** 略 ▸ 爆：新建 ▸ 查：查新建
  - 闸门：状态=active｜核心=否｜类型=新建｜特征=任意
- **Q3｜AI 自动动作类题？** 略 ▸ 爆：AI 挂了 ▸ 查：查必做动作
  - 闸门：状态=active｜核心=否｜类型=任意｜特征=AI自动动作
- **Q4｜已作废的题？** 略 ▸ 爆：不会 ▸ 查：不必
  - 闸门：状态=retired｜核心=否｜类型=任意｜特征=度量统计

## B 可靠性与容错
- **Q5｜备份双校验？** 略 ▸ 爆：回滚时 ▸ 查：文件数+体积
  - 闸门：状态=active｜核心=是｜类型=任意｜特征=任意
"""

FIX_CAND = """# 候选区（测试夹具）

## 候选 C1（2026-01-01 登记）

- **C1｜没验证过的候选题？** 略 ▸ 爆：某天 ▸ 查：某查法
  - 闸门：状态=candidate｜核心=否｜类型=任意｜特征=无

## 候选 C2（2026-01-01 登记）

- **C2｜与 Q1 重影的题？** 略 ▸ 爆：某天 ▸ 查：某查法
  - 闸门：状态=candidate｜核心=否｜类型=任意｜特征=无
  - 重影待裁决：与 Q1 近似（近似题 0.71），人工裁决前不许 promote
"""

FIX_LOCAL = """# 候选区 · 本机私有（测试夹具，同步器按 *.local.md 排除）

## 候选 C3（2026-01-01 登记）

- **C3｜个人项目特有的候选题？** 略 ▸ 爆：本机 ▸ 查：本机查法
  - 闸门：状态=candidate｜核心=否｜类型=存量｜特征=对外发布
"""

FIX_SKILL = """---
name: fixture
metadata:
  version: v9.9
---

# 夹具

<!-- AUTO-BEGIN 容量块（scripts/qbank.py render --apply 生成，手改即被 check 判红） -->
<!-- AUTO-END -->
"""

GOOD_REPORT = """# 《小计划》审判报告（第 1 轮）

- 结论：可开工
- 轮次：1
- 一句话结论：能动工，无致命阻塞

## 一、本轮画像与选题依据
- 画像：通用（无画像）
- 加载范围：命中 3 ／ 总数 4（跳过 1 条）
- 选题命令：`py scripts/qbank.py select --profile 通用`

## 二、逐类裁决表
| 类别 | 名称 | 本类题号 | 裁决 | 备注 |
| --- | --- | --- | --- | --- |
| A | 结构性矛盾 | Q1 Q2 | 全通过 | |

## 六、沉淀
- 沉淀：本轮无新题
"""

BAD_REPORT_NOEXIT = """# 《复杂计划》审判报告（第 2 轮）

- 结论：附条件开工
- 轮次：2
- 画像：类型=新建｜特征=AI自动动作,量化承诺
- 加载范围：命中 4 ／ 总数 4（跳过 0 条）

## 三、阻塞项

### B-1 外部数据源还没给口径
- 级别：致命
- 处置：外待
- 解除条件：对方给出字段口径书面确认
- 责任方：禾
- 时机／期限：10-31 前
- 验证方式：看邮件回复原文

### B-2 验收数字不可证伪
- 级别：重要
- 处置：残险
- 解除条件：接受「上线后第一个月按实测补口径」
- 责任方：天
- 时机／期限：2026-11-15
- 验证方式：复盘会上看实测表
- 为什么可接受：范围只在对外材料，不影响功能
- 爆炸半径上限：最坏是 README 一句数字返工
- 发现机制：每月复盘必查项
- 复核日期：2026-11-15
"""

BAD_REPORT_OVERROUND = BAD_REPORT_NOEXIT.replace("- 结论：附条件开工", "- 结论：附条件开工").replace(
    "- 轮次：2", "- 轮次：4")

BAD_REPORT_RAW = """# 《烂报告》审判报告（第 4 轮）

- 结论：附条件开工
- 轮次：4
- 画像：类型=新建｜特征=量化承诺
- 加载范围：命中 4 ／ 总数 4

### B-1 缺件示例
- 级别：致命
- 处置：未解除

### B-2 外待缺两件套
- 级别：重要
- 处置：外待
- 解除条件：等对方回口径
- 责任方：禾
- 时机／期限：月底
- 验证方式：看回复

## 六、沉淀
- 沉淀：本轮新增 Q99（已回库）
"""


def selftest():
    results = []

    def chk(name, ok, detail=""):
        results.append((name, bool(ok), detail))

    work = tempfile.mkdtemp(prefix="qbank-selftest-")
    try:
        root = os.path.join(work, "skill")
        os.makedirs(os.path.join(root, "references"))
        os.makedirs(os.path.join(root, "assets"))
        io.open(os.path.join(root, "references/审判问题库.md"), "w",
                encoding="utf-8", newline="\n").write(FIX_BANK)
        io.open(os.path.join(root, "references/候选区.md"), "w",
                encoding="utf-8", newline="\n").write(FIX_CAND)
        io.open(os.path.join(root, "references/候选区.local.md"), "w",
                encoding="utf-8", newline="\n").write(FIX_LOCAL)
        io.open(os.path.join(root, "SKILL.md"), "w",
                encoding="utf-8", newline="\n").write(FIX_SKILL)

        # A1 解析：正式题 5（含 1 条 retired）、候选 3（公开 2＋本机 1）
        st = load_all(root)
        chk("A1 现扫正式题=5", len(st["bank"]) == 5, str(len(st["bank"])))
        chk("A1b active=4／retired=1",
            len([i for i in st["bank"] if i.status == "active"]) == 4
            and len([i for i in st["bank"] if i.status == "retired"]) == 1)
        chk("A2 候选（公开2+本机1）", len(st["cand"]) == 2 and len(st["local"]) == 1)
        chk("A3 解析错误为 0", not st["errs"], str(st["errs"]))

        # A4 选题制：通用画像只取核心题，且不含闸门题
        act = [i for i in st["bank"] if i.status == "active"]
        picked = select_items(st["bank"], None, set(), False)
        ids = sorted(i.id for i, _ in picked)
        chk("A5 通用画像＝核心题 Q1 Q5", ids == ["Q1", "Q5"], " ".join(ids))
        chk("A6 retired 不在输出", "Q4" not in ids)
        chk("A7 入选原因带核心字样", all("核心题" in w for _, w in picked))

        # A8 画像命中闸门
        p2 = sorted(i.id for i, _ in select_items(st["bank"], "新建", {"AI自动动作"}, False))
        chk("A8 类型闸命中 Q2", "Q2" in p2, " ".join(p2))
        chk("A9 特征闸命中 Q3", "Q3" in p2, " ".join(p2))
        p3 = sorted(i.id for i, _ in select_items(st["bank"], "存量", set(), False))
        chk("A10 类型不匹配则不入选（存量画像拿不到 Q2）", "Q2" not in p3 and "Q3" not in p3)

        # A11 生成物：render 后 check 必须绿
        red, warn = check_store(st, root)
        chk("A12 render 前生成物缺位判红", any("R7" in r for r in red), str(red[:2]))
        for path, should in plan_writes(st, root):
            write_text(path, should)
        st2 = load_all(root)
        red2, warn2 = check_store(st2, root)
        chk("A13 render --apply 后无红项", not red2, "｜".join(red2[:3]))
        chk("A14 索引生成在场", os.path.isfile(os.path.join(root, INDEX_REL)))
        chk("A15 模板生成在场", os.path.isfile(os.path.join(root, TEMPLATE_REL)))
        tpl = read_text(os.path.join(root, TEMPLATE_REL))
        chk("A16 模板含 active 类别行 Q1/Q5", "Q1" in tpl and "Q5" in tpl)
        chk("A17 retired Q4 不在模板（排除面）", "Q4" not in tpl)
        chk("A18 候选 C1 不在模板", "C1" not in tpl)
        chk("A19 索引含核心题表", "通用核心题子集" in read_text(os.path.join(root, INDEX_REL)))

        # A20 散文复述计数即红（写对也红）
        bp = os.path.join(root, "references/审判问题库.md")
        t = read_text(bp).replace("# 审判问题库（测试夹具）",
                                  "# 审判问题库（测试夹具）\n\n> 当前 4 题，够用。")
        write_text(bp, t)
        red3, _ = check_store(load_all(root), root)
        chk("A20 散文复述容量判红", any("R4" in r for r in red3), str([r for r in red3 if "R4" in r][:1]))
        # A20b 把数字改对也照样红（防「写对了就放过」的恒真尺子）
        t2 = read_text(bp).replace("> 当前 4 题，够用。", "> 当前 3 题，够用。")
        write_text(bp, t2)
        red4, _ = check_store(load_all(root), root)
        chk("A21 换个数字仍判红（不是靠数值对不上）", any("R4" in r for r in red4))
        write_text(bp, read_text(bp).replace("\n> 当前 3 题，够用。", ""))
        chk("A22 清掉复述后回到绿", not [r for r in check_store(load_all(root), root)[0] if "R4" in r])

        # A23 闸门非法值
        bpt = read_text(bp)
        write_text(bp, bpt.replace("特征=AI自动动作", "特征=AI自动动作,不存在特征"))
        red5, _ = check_store(load_all(root), root)
        chk("A23 特征越界判红", any("R2" in r and "越界" in r for r in red5))
        write_text(bp, read_text(bp).replace("特征=AI自动动作,不存在特征", "特征=AI自动动作"))

        # A24 核心 ⟺ 无闸 双向（把 Q2 的闸门拆掉但不标核心 ⇒ 必须红）
        snap = read_text(bp)
        write_text(bp, snap.replace(
            "  - 闸门：状态=active｜核心=否｜类型=新建｜特征=任意",
            "  - 闸门：状态=active｜核心=否｜类型=任意｜特征=任意"))
        chk("A25 拆掉闸门却不标核心 ⇒ 判红",
            any("不是核心题" in r for r in check_store(load_all(root), root)[0]))
        write_text(bp, read_text(bp).replace(
            "  - 闸门：状态=active｜核心=否｜类型=任意｜特征=任意",
            "  - 闸门：状态=active｜核心=是｜类型=任意｜特征=任意"))
        chk("A25b 同一题标了核心却…已无闸 ⇒ 绿（另一半方向不恒红）",
            not any("Q2" in r and "R2" in r for r in check_store(load_all(root), root)[0]))
        write_text(bp, snap)

        # A26 ID 缺口：抽掉中间的 Q3（删最大号不叫跳号，那样尺子永远不会红）
        write_text(bp, re.sub(r"^- \*\*Q3｜[^\n]*\n^  - 闸门：[^\n]*\n", "", read_text(bp),
                              flags=re.M))
        red6, _ = check_store(load_all(root), root)
        chk("A27 抽掉 Q3 造成跳号 ⇒ 判红", any("R1" in r and "缺口" in r for r in red6),
            str([r for r in red6 if "R1" in r][:1]))
        write_text(bp, snap)
        chk("A27b 还原后跳号红项消失（证明红项来自缺口本身）",
            not any("R1" in r for r in check_store(load_all(root), root)[0]))

        # A28 分母闸：空库直接退 2
        empty = os.path.join(work, "empty")
        os.makedirs(os.path.join(empty, "references"))
        io.open(os.path.join(empty, "references/审判问题库.md"), "w",
                encoding="utf-8", newline="\n").write("# 空库\n")
        try:
            check_store(load_all(empty), empty)
            chk("A28 空库不退出＝尺子坏了", False)
        except SystemExit as e:
            chk("A28 空库分母闸退 2", e.code == 2, str(e.code))

        # A29 candidate 混进正式题库 ⇒ 红
        write_text(bp, read_text(bp) + "\n- **Q6｜偷跑进来的候选题？** 略 ▸ 爆：x ▸ 查：y\n"
                                       "  - 闸门：状态=candidate｜核心=否｜类型=任意｜特征=无\n")
        red7, _ = check_store(load_all(root), root)
        chk("A30 候选状态躺在正式题库判红", any("candidate 却躺在正式题库" in r for r in red7))
        write_text(bp, "\n".join(l for l in read_text(bp).split("\n")
                                 if "偷跑进来的候选题" not in l
                                 and "闸门：状态=candidate" not in l))

        # A31 add：去重闸能红
        code = cmd_add(root, load_all(root), mkadd(title="铁则有无过度承诺",
                                                  why="w", boom="b", check="c"))
        chk("A31 与 Q1 同指纹被去重闸拦住（退 1）", code == 1, str(code))
        # A32 add：要素不齐直接拒
        try:
            cmd_add(root, load_all(root), mkadd(title="缺要素题", why="", boom="b", check="c"))
            chk("A32 缺 why 应退出", False)
        except SystemExit as e:
            chk("A32 add 缺要素退 2", e.code == 2, str(e.code))
        # A33 add 真写候选区（正式库数不变）
        before = len(load_all(root)["bank"])
        cmd_add(root, load_all(root), mkadd(title="新维度题？", why="w", boom="b",
                                            check="c", trait="量化承诺", apply=True))
        st4 = load_all(root)
        chk("A33 add 后正式库条数不变", len(st4["bank"]) == before, "%d→%d" % (before, len(st4["bank"])))
        chk("A34 add 后候选区在册 +1", len(st4["cand"]) == 3, str(len(st4["cand"])))
        # A34 幂等：同题再提交＝no-op
        cmd_add(root, load_all(root), mkadd(title="新维度题", why="w", boom="b",
                                            check="c", trait="量化承诺", apply=True))
        chk("A35 同题重复提交＝no-op（候选数不变）", len(load_all(root)["cand"]) == 3)
        # A36 personal ⇒ 写本机候选区
        cmd_add(root, load_all(root), mkadd(title="个人专有题", why="w", boom="b",
                                            check="c", personal=True, apply=True))
        st5 = load_all(root)
        chk("A36 personal 候选题落本机候选区", len(st5["local"]) == 2, str(len(st5["local"])))
        # A37 发布面：本机候选区文件名命中同步器排除模式（改名即红，常量写法不会自证）
        loc_name = os.path.basename(CAND_LOCAL_REL)
        pub_name = os.path.basename(CAND_PUBLIC_REL)
        chk("A37 本机候选区被发布排除模式命中（发布侧必不含私有题）",
            any(fnmatch.fnmatch(loc_name, pat) for pat in PUBLISH_EXCLUDE)
            and not any(fnmatch.fnmatch(pub_name, pat) for pat in PUBLISH_EXCLUDE),
            "%s 对 %s" % (loc_name, " / ".join(PUBLISH_EXCLUDE)))
        chk("A37b 关掉本机候选区读取后私有题不进扫描",
            "个人专有题" not in " ".join(i.title for i in load_all(root)["cand"]))
        # A38 越界特征值退 2
        try:
            cmd_add(root, load_all(root), mkadd(title="乱值题", why="w", boom="b",
                                                check="c", trait="不存在"))
            chk("A38 特征越界应退出", False)
        except SystemExit as e:
            chk("A38 add 特征越界退 2", e.code == 2, str(e.code))

        # A38b 重复 --trait/--type 不许静默丢值（2026-10-14 实测：真 CLI 连给两个 --trait
        # 只留最后一个，闸门行少一个特征→选题面永久漏检，且零报警）
        chk("A38e capture_exit 本身可信：返回码原样透传（否则下面几条的 code==0 是假判据）",
            capture_exit(lambda: 7) == (7, "") and capture_exit(lambda: None)[0] == 0,
            str((capture_exit(lambda: 7), capture_exit(lambda: None))))
        code38b, out38b = capture_exit(lambda: cmd_add(
            root, load_all(root), mkadd(title="多值题", why="w", boom="b", check="c",
                                         trait=["度量统计", "单一事实源"],
                                         type=["新建", "存量"])))
        gate38b = [l for l in out38b.split("\n") if "闸门" in l]
        chk("A38b 干跑输出里两个特征都在（列表写法＝重复传参的等价形态）",
            code38b == 0 and any("特征=度量统计,单一事实源" in l for l in gate38b),
            "｜".join(gate38b) or out38b.strip()[:80])
        chk("A38c 干跑输出里两个类型都在", any("类型=新建,存量" in l for l in gate38b),
            "｜".join(gate38b))
        chk("A38d 干跑确实没落盘（候选数不变）", len(load_all(root)["cand"]) == 3,
            str(len(load_all(root)["cand"])))

        # A39 promote：缺 scope 拒 / 带 scope 分配新号 / 候选区移出
        try:
            cmd_promote(root, load_all(root), mkprom(cid="C3", cat="A", scope=""))
            chk("A39 缺 scope 应退出", False)
        except SystemExit as e:
            chk("A39 promote 缺适用范围退 2", e.code == 2, str(e.code))
        cmd_promote(root, load_all(root), mkprom(cid="C3", cat="A", scope="新建类项目适用", apply=True))
        st6 = load_all(root)
        new_ids = sorted(i.id for i in st6["bank"])
        chk("A40 promote 后新号进正式库", "Q6" in new_ids, " ".join(new_ids))
        chk("A41 promote 后候选区那条已移出（本机候选区 2→1，公开候选区不受牵连）",
            len(st6["local"]) == 1 and len(st6["cand"]) == 3,
            "local=%d cand=%d" % (len(st6["local"]), len(st6["cand"])))
        chk("A42 promote 写入了适用范围", any("适用范围" in i.lead or "▸ 适用范围" in
                                           "\n".join(i.lines) for i in st6["bank"] if i.id == "Q6"))
        # A43 promote 重影未裁决不许升（🔴 断言要认红项文本：退 2 可能来自缺 scope／特征占位，只比退出码＝假 PASS）
        code43, out43 = capture_exit(lambda: cmd_promote(
            root, load_all(root), mkprom(cid="C2", cat="A", scope="适用")))
        chk("A43 带「重影待裁决」注记的候选被重影闸拦住（红因对得上）",
            code43 == 2 and "重影待裁决" in out43, "%s｜%s" % (code43, out43.strip()[:70]))
        # A43b 特征仍是占位 `无` 的候选不许升（C1 没带注记，红因必须是特征占位）
        code43b, out43b = capture_exit(lambda: cmd_promote(
            root, load_all(root), mkprom(cid="C1", cat="A", scope="适用")))
        chk("A43b 特征=占位`无` 的候选被适用范围闸拦住（红因对得上）",
            code43b == 2 and "特征还是占位" in out43b, "%s｜%s" % (code43b, out43b.strip()[:70]))

        # A43c 候选号跨文件重号 ⇒ check 判红 + promote 拒绝（不许静默选一条）
        amb = os.path.join(work, "ambig")
        os.makedirs(os.path.join(amb, "references"))
        dup_local = FIX_LOCAL.replace(" C3（", " C1（").replace("**C3｜", "**C1｜")
        write_text(os.path.join(amb, "references/审判问题库.md"), FIX_BANK)
        write_text(os.path.join(amb, "references/候选区.md"), FIX_CAND)
        write_text(os.path.join(amb, "references/候选区.local.md"), dup_local)
        write_text(os.path.join(amb, "SKILL.md"), FIX_SKILL)
        st_amb = load_all(amb)
        amb_red, _ar = check_store(st_amb, amb)
        chk("A43c 候选号跨文件重号 ⇒ check 判红（R5b）",
            any("R5b" in r for r in amb_red), str([r for r in amb_red if "R5b" in r][:1]))
        code_amb, out_amb = capture_exit(lambda: cmd_promote(
            amb, st_amb, mkprom(cid="C1", cat="A", scope="适用")))
        chk("A43c2 promote 遇重号拒绝落盘（不静默选一条）",
            code_amb == 2 and "各有一条" in out_amb,
            "%s｜%s" % (code_amb, out_amb.strip()[:70]))

        # A44~A52 gate
        red8, warn8 = [], []
        act_n = len([i for i in load_all(root)["bank"] if i.status == "active"])
        gate_report(GOOD_REPORT, act_n, red8, warn8)
        chk("A44 合规报告：红 0", not red8, "｜".join(red8))
        red9, w9 = [], []
        gate_report(BAD_REPORT_RAW, act_n, red9, w9)
        chk("A45 缺出口＋直接编 Q 号＋轮次超限 ⇒ 多条红", len(red9) >= 3, str(red9))
        chk("A46 沉淀行直接给 Q 号被抓", any("G8" in r for r in red9))
        chk("A47 轮次超限被抓", any("G7" in r for r in red9))
        chk("A48 处置=未解除且无出口 ⇒ 结论不许附条件开工被抓（红因点名 B-1）", any(
            ("G10" in r and "B-1" in r) for r in red9), str(red9))
        red10, w10 = [], []
        gate_report(BAD_REPORT_NOEXIT, act_n, red10, w10)
        chk("A48b 出口件齐的报告不得白报 G10（证明该判据能绿，不是恒红）",
            not any("G10" in r for r in red10), str([r for r in red10 if "G10" in r]))
        red60, _w60 = [], []
        gate_report(BAD_REPORT_RAW.replace("- 结论：附条件开工", "- 结论：暂缓"),
                    act_n, red60, _w60)
        chk("A60 未解除项改判暂缓 ⇒ G10 消失（暂缓承认挂着，不算绕过）",
            not any("G10" in r for r in red60), str([r for r in red60 if "G10" in r]))
        chk("A49 外待缺超时策略被抓", any("等待对象" in r or "超时后动作" in r for r in red10),
            str(red10))
        chk("A50 残险四件套齐 ⇒ 不再报缺件", not any(
            "B-2" in r and "G2" in r for r in red10), str([r for r in red10 if "B-2" in r]))
        red11, _ = [], []
        gate_report(BAD_REPORT_OVERROUND.replace("- 结论：附条件开工", "- 结论：暂缓"), act_n, red11, _)
        chk("A51 暂缓缺重启条件被抓", any("重启条件" in r for r in red11), str(red11))
        # A52 全量载入的警告面
        red12, warn12 = [], []
        gate_report(GOOD_REPORT.replace("命中 3 ／ 总数 4（跳过 1 条）",
                                        "命中 4 ／ 总数 4（跳过 0 条）")
                    .replace("- 画像：通用（无画像）", "- 画像：类型=新建｜特征=量化承诺"),
                    act_n, red12, warn12)
        chk("A52 命中＝总数时出警告（不是红）", any("G9" in w for w in warn12), str(warn12))

        # A53 render 缺 AUTO 标记 ⇒ 明确报错不猜位置
        nomark = os.path.join(work, "nomark")
        os.makedirs(os.path.join(nomark, "references"))
        io.open(os.path.join(nomark, "references/审判问题库.md"), "w",
                encoding="utf-8", newline="\n").write("# 库\n" + FIX_BANK.split("\n\n", 2)[2])
        io.open(os.path.join(nomark, "SKILL.md"), "w", encoding="utf-8", newline="\n").write(
            "---\nname: x\n---\n# x\n")
        io.open(os.path.join(nomark, "references/候选区.md"), "w",
                encoding="utf-8", newline="\n").write("# 候选\n")
        try:
            plan_writes(load_all(nomark), nomark)
            chk("A53 缺 AUTO 标记应报错", False)
        except RuntimeError as e:
            chk("A53 缺 AUTO 标记直接报错（不猜插入位置）", "AUTO" in str(e), str(e)[:60])

        # A53b 施工前的真实形状：check 不许抛栈，要报成可读的红项（未迁移的库就是这副样子）
        try:
            nred, _nwarn = check_store(load_all(nomark), nomark)
            chk("A53b 缺标记 ⇒ check 报红项不抛栈（未施工库可体检）",
                any("生成物断链" in r for r in nred), str(nred[:1]))
        except RuntimeError as e:
            chk("A53b 缺标记时 check 抛 traceback＝尺子坏了", False, str(e)[:60])

        # A54~A56 排除面的输出形状（retired/candidate 不进 select／模板／索引第三节）
        st7 = load_all(root)
        idx = read_text(os.path.join(root, INDEX_REL))
        chk("A54 索引第七节列出 retired", "retired" in idx)
        chk("A55 select 输出不含候选号", all(i.id.startswith("Q") for i, _ in select_items(
            st7["bank"], None, set(), True)))
        unp = select_items(st7["bank"], "存量", {"对外发布"}, False)
        chk("A56 retired Q4 即便画像命中也不入选（特征=度量统计，不在结果里）",
            all(i.id != "Q4" for i, _ in unp))

        # A57 泄漏面：本机私有候选题的问题句不许进生成物（生成物随包外发）
        priv_titles = [i.title for i in load_all(root)["local"] if i.title]
        chk("A57 索引里不含本机私有候选题的问题句（已打码）",
            all(t not in read_text(os.path.join(root, INDEX_REL)) for t in priv_titles)
            and any(t for t in priv_titles), "；".join(priv_titles[:2]))
        chk("A57b 索引里有私有候选题的打码占位行",
            LOCAL_MASK in read_text(os.path.join(root, INDEX_REL)))
        # A58 尺子能红：手工把私有题塞进生成物 ⇒ R10 判红（不塞就永远不会红）
        ip = os.path.join(root, INDEX_REL)
        itext = read_text(ip)
        write_text(ip, itext + "\n\n漏抄的一条：" + priv_titles[0] + "\n")
        leak_red, _lr = check_store(load_all(root), root)
        chk("A58 私有题问题句混进生成物 ⇒ R10 判红",
            any("R10" in r for r in leak_red), str([r for r in leak_red if "R10" in r][:1]))
        write_text(ip, itext)
        chk("A58b 撤掉泄漏行后 R10 消失（红项来自内容本身，不是恒红）",
            not any("R10" in r for r in check_store(load_all(root), root)[0]))

        # A59 生成物的父目录不在场 ⇒ render 必须把它建出来（真实库首跑就撞过这个坑：
        #     技能根目录里没有 assets/，写盘直接 FileNotFoundError 抛出，红得毫无信息量）
        assets_dir = os.path.join(root, "assets")
        shutil.rmtree(assets_dir)
        chk("A59 生成物目录已删干净（前置条件成立，否则这条断言是空的）",
            not os.path.isdir(assets_dir))
        st9 = load_all(root)
        wrote9 = 0
        for path, should in plan_writes(st9, root):
            if write_text(path, should):
                wrote9 += 1
        chk("A59b 缺 assets/ 时 render 自建父目录并全部落盘",
            os.path.isdir(assets_dir) and wrote9 == len(plan_writes(load_all(root), root)),
            "写入 %d 件" % wrote9)
        chk("A59c 重建后体检回到无红项",
            not check_store(load_all(root), root)[0],
            "｜".join(check_store(load_all(root), root)[0][:2]))

        ok = sum(1 for _, o, _ in results if o)
        bad = [n for n, o, d in results if not o]
        print("断言 %d 条：PASS=%d FAIL=%d" % (len(results), ok, len(bad)))
        for n, o, d in results:
            if not o:
                print("  [FAIL] %s ｜ %s" % (n, d))
        print("SUITE_EXIT=%d" % (0 if not bad else 1))
        return 0 if not bad else 1
    finally:
        shutil.rmtree(work, ignore_errors=True)


class _Args(object):
    pass


def capture_exit(fn):
    """跑 fn，返回 (退出码, 它打印的原文)。红项断言用得上：退出码 2 可能来自任何一条 die()，
    只有把打印出来的那句话也钉住，才知道拦住的确实是设计中的那条闸（不是撞上了隔壁判据）。"""
    buf = io.StringIO()
    code = None
    try:
        with contextlib.redirect_stdout(buf):
            ret = fn()
        if not isinstance(ret, int):
            ret = 0
        code = ret
    except SystemExit as e:
        code = e.code
    return code, buf.getvalue()


def mkadd(title, why, boom, check, case="", source="", type=None, trait=None,
           personal=False, date="", force=False, apply=False):
    a = _Args()
    a.title, a.why, a.boom, a.check, a.case, a.source = title, why, boom, check, case, source
    a.type, a.trait, a.personal, a.date, a.force, a.apply = type, trait, personal, date, force, apply
    return a


def mkprom(cid, cat, scope, core="否", apply=False, type=None, trait=None):
    a = _Args()
    a.id, a.cat, a.scope, a.core, a.apply = cid, cat, scope, core, apply
    a.type, a.trait = type, trait
    return a


if __name__ == "__main__":
    sys.exit(main())
