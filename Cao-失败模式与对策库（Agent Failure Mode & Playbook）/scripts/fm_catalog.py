#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""fm_catalog.py —— 失败模式目录的「现扫生成器 + 体检器」（零依赖，默认只读）。

一句话：库里有多少条死法、谁引用了谁、某类有几条，以前全靠人手抄进散文，抄一次就漂移一次
（2026-10-11 外部审查抓出的 43／41 就是这一类）。本脚本把这三件事改成从条目现扫生成：
计数、索引、交叉引用只有一个来源——`references/01-failure-modes/` 里的条目本身。

子命令（默认全部只读，只有 render --apply 会写盘）：
  scan      把 01 目录的条目解析成结构化记录（可 --json 机读）
  counts    打印现扫容量总账（死法／对策／术语／案例／清单／模板／类别）
  select    按画像（工具副作用／并发／租户／数据敏感度）只挑适用条目，并打印加载范围
  render    生成 `references/01-failure-modes/_目录索引.md` 与 SKILL.md 的容量块（默认干跑，--apply 才写）
  check     结构体检：字段齐不齐、编号连不连、引用指不指得到、散文里有没有复述计数
  selftest  用合成样例（临时目录，绝不碰真实库）自证每条判据都能红也能绿

退出码：0＝通过　1＝有红项　2＝用法错误或「分母闸」失效（扫到 0 条时禁止打「无红项」，
因为空集上的任何断言都是恒真的，等于没测）。
"""

import argparse
import difflib
import io
import json
import os
import re
import sys
import tempfile
from pathlib import Path

# ---------------------------------------------------------------- 路径与常量

SKILL_ROOT = Path(__file__).resolve().parent.parent
FM_SUBDIR = "references/01-failure-modes"
PATTERNS_REL = "references/02-patterns/对策模式库.md"
TERMS_REL = "references/03-terms/术语速查表.md"
INCIDENTS_REL = "references/04-incidents/事故案例集.md"
CHECKLISTS_SUBDIR = "references/05-checklists"
PROMPTS_SUBDIR = "references/06-prompts"
INDEX_NAME = "_目录索引.md"
PROMPTS_MAIN_REL = "references/06-prompts/生成问题清单.md"

REQUIRED_FIELDS = ("现象", "术语", "根因", "对策", "最坏后果", "检查问题", "相关", "适用条件")
OPTIONAL_FIELDS = ("案例", "运行边界")
AXES = ("副作用", "并发", "租户", "敏感度")
VOCAB = {
    "副作用": ("任意", "有写"),
    "并发": ("任意", "多流"),
    "租户": ("任意", "多租户"),
    "敏感度": ("任意", "敏感"),
}
# 画像档位 → 条目要求的满足关系：条目写「任意」永远成立；写具体条件时要求画像到相应档位。
AXIS_MEETS = {
    "副作用": {"有写": lambda v: v == "写"},
    "并发": {"多流": lambda v: v == "多"},
    "租户": {"多租户": lambda v: v == "多"},
    "敏感度": {"敏感": lambda v: v == "敏感"},
}
PROFILE_DEFAULT = {"副作用": "无", "并发": "单", "租户": "单", "敏感度": "公开"}
PROFILE_CHOICES = {"副作用": ("无", "写"), "并发": ("单", "多"), "租户": ("单", "多"), "敏感度": ("公开", "敏感")}

ID_RE = re.compile(r"^###\s+([A-Z]+)-(\d+)\s+(\S.*)$")
FIELD_RE = re.compile(r"^-\s*([^：:]+)[：:]\s*(.*)$")
AXIS_RE = re.compile(r"([副作用并发租户敏感度]{2,4})=([^｜|]+)")
FM_ID_RE = re.compile(r"\b(?:LOOP|ERR|COST|SEC|CTX|CONC|GOV)-\d+\b")
P_ID_RE = re.compile(r"\bP-\d+\b")
CASE_ID_RE = re.compile(r"\b[RF]-\d+\b")
CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
CN_INT = {v: k for k, v in CN_NUM.items()}

# 复述型容量计数：散文里写「43 种死法／26 条对策／库里 41 条／先过这 5 条」这类形状。
CAPACITY_NOUNS = "(?:死法|失败模式|对策|术语|案例|事故|检查清单|提问模板)"
CAPACITY_PATTERNS = (
    re.compile(r"(\d+)\s*(?:种|条|个|例)\s*" + CAPACITY_NOUNS),
    re.compile(CAPACITY_NOUNS + r"[^。；\n]{0,6}?(\d+)\s*(?:种|条|个|例)"),
    re.compile(r"[（(]\s*(\d+)\s*条\s*[）)]"),
    re.compile(r"库里\s*(\d+)\s*条"),
    re.compile(r"先过这\s*(\d+)\s*条"),
    re.compile(r"(\d+)\s*(?:种|条|个|例)\s*" + CAPACITY_NOUNS + r"[^。；\n]{0,4}"),
)
# 分类数复述：只认紧邻「死法」的中文数词，且排除「这一类死法」这类指示用法。
CLASS_RE = re.compile(r"(?<![这那每同本该哪各全两gnos])([二三四五六七八九十])类死法")
DIM_RE = re.compile(r"([一二三四五六七八九十])维度")
# 引用外部权威自带的数字（如 MAST 自己的 16 小类）是引文，不是本库容量；豁免面在 check 输出里明写。
EXTERNAL_CITE = ("MAST", "arXiv", "Berkeley", "Simon Willison", "Agentic AI")
AUTO_BEGIN = "<!-- AUTO-BEGIN 容量块（fm_catalog.py render --apply 生成，手改即被 check 判红） -->"
AUTO_END = "<!-- AUTO-END -->"

DRY = "干跑（未写盘；要写盘加 --apply）"


def read_text(path):
    return path.read_bytes().decode("utf-8", errors="replace")


def write_lf(path, text):
    """合成样例一律写字节、固定 LF：Path.write_text 在 Windows 会把 \\n 译成 \\r\\n，
    于是自测里按 LF 写的替换串静默不命中，红样本悄悄变绿样本（假 PASS）。"""
    path.write_bytes(text.replace("\r\n", "\n").encode("utf-8"))


def die(msg, code=2):
    print("红｜" + msg)
    sys.exit(code)


def clause_of(line, start):
    """取 start 所在的那个逗号级分句。外部口径的豁免只给「数字自己那一句」，
    不给整行——同一行后半句提到 MAST，不能把前半句的本库条数一起放掉。"""
    pos, segs = 0, []
    for m in re.finditer(r"[，,；;。\n]", line):
        segs.append((pos, m.start()))
        pos = m.end()
    segs.append((pos, len(line)))
    for a, b in segs:
        if a <= start < b:
            return line[a:b]
    return line


# ---------------------------------------------------------------- 解析

def fm_files(root):
    d = Path(root) / FM_SUBDIR
    if not d.is_dir():
        return []
    return sorted(p for p in d.glob("*.md") if not p.name.startswith("_"))


def parse_entries(root):
    """把 01 目录解析成记录列表。每条：id/prefix/n/title/category/file/fields/axes/lineno。"""
    entries = []
    for path in fm_files(root):
        category = path.stem[3:] if path.stem.startswith("FM-") else path.stem
        text = read_text(path)
        cur = None
        for ln, line in enumerate(text.splitlines(), 1):
            m = ID_RE.match(line.rstrip())
            if m:
                if cur:
                    entries.append(cur)
                cur = {"prefix": m.group(1), "num": int(m.group(2)), "n": "%s-%s" % (m.group(1), m.group(2)),
                       "title": m.group(3).strip(), "category": category, "file": path.name, "lineno": ln,
                       "fields": {}}
                continue
            if cur is None:
                continue
            if line.startswith("#"):
                entries.append(cur)
                cur = None
                continue
            fm = FIELD_RE.match(line.strip())
            if fm and line.strip().startswith("-"):
                key, val = fm.group(1).strip(), fm.group(2).strip()
                if key in REQUIRED_FIELDS or key in OPTIONAL_FIELDS:
                    cur["fields"][key] = val
        if cur:
            entries.append(cur)
    for e in entries:
        e["axes"] = parse_axes(e["fields"].get("适用条件", ""))
    return entries


def parse_axes(raw):
    out = {}
    for m in AXIS_RE.finditer(raw or ""):
        out[m.group(1)] = m.group(2).strip()
    return out


def parse_patterns(root):
    """02 药柜：编号 → {name, guard(FM 编号列表)}，顺序保留。"""
    path = Path(root) / PATTERNS_REL
    if not path.is_file():
        return []
    rows, cur = [], None
    for line in read_text(path).splitlines():
        m = re.match(r"^###\s+(P-\d+)\s*(.*?)\s*$", line.strip())
        if m:
            if cur:
                rows.append(cur)
            cur = {"n": m.group(1), "name": m.group(2), "guards": []}
            continue
        if cur is not None and "**防**" in line:
            cur["guards"] = FM_ID_RE.findall(line)
    if cur:
        rows.append(cur)
    return rows


def parse_terms(root):
    """03 术语表：数表数据行（排除表头与分隔行）。"""
    path = Path(root) / TERMS_REL
    if not path.is_file():
        return []
    rows = []
    for line in read_text(path).splitlines():
        s = line.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if not cells or all(set(c) <= set("-: ") for c in cells):
            continue
        if cells[0] in ("术语",):
            continue
        if len(cells) >= 2 and cells[1] == "中文":
            continue
        rows.append({"term": cells[0], "cn": cells[1] if len(cells) > 1 else "",
                     "refs": cells[-1] if len(cells) >= 4 else ""})
    return rows


def parse_cases(root):
    path = Path(root) / INCIDENTS_REL
    if not path.is_file():
        return []
    out = []
    for line in read_text(path).splitlines():
        m = re.match(r"^###\s+([RF]-\d+)\s*(.*?)\s*$", line.strip())
        if m:
            out.append({"n": m.group(1), "title": m.group(2)})
    return out


def counts_of(root):
    entries = parse_entries(root)
    cats = {}
    for e in entries:
        cats.setdefault(e["prefix"], {"category": e["category"], "file": e["file"], "n": 0, "nums": []})
        cats[e["prefix"]]["n"] += 1
        cats[e["prefix"]]["nums"].append(e["num"])
    files5 = sorted(p.name for p in (Path(root) / CHECKLISTS_SUBDIR).glob("*.md"))
    files6 = sorted(p.name for p in (Path(root) / PROMPTS_SUBDIR).glob("*.md"))
    return {
        "fm": len(entries),
        "patterns": len(parse_patterns(root)),
        "terms": len(parse_terms(root)),
        "cases": len(parse_cases(root)),
        "classes": len(fm_files(root)),
        "checklists": len(files5),
        "prompts": len(files6),
        "checklist_files": files5,
        "prompt_files": files6,
        "per_class": cats,
    }


# ---------------------------------------------------------------- 分母闸

def gate(root):
    """扫到 0 条就退 2：空集上的断言恒真，不能当「通过」。"""
    entries = parse_entries(root)
    d = Path(root) / FM_SUBDIR
    if not d.is_dir():
        die("分母闸失效：找不到目录 %s（真实路径请传 --root 指到技能根）" % d)
    if not entries:
        die("分母闸失效：%s 里解析出 0 条死法条目（### X-N 标题行一条都没有）——"
            "在这种前提下打「无红项」是空集恒绿，不算通过" % FM_SUBDIR)
    return entries


# ---------------------------------------------------------------- 选择器

def applicable(entry, profile):
    for axis in AXES:
        want = entry["axes"].get(axis, "任意")
        if want == "任意":
            continue
        test = AXIS_MEETS[axis].get(want)
        if test is None:
            return True  # 词表外的值由 check 判红，这里不误杀
        if not test(profile[axis]):
            return False
    return True


def do_select(root, profile, emit):
    entries = gate(root)
    hit = [e for e in entries if applicable(e, profile)]
    print("画像（profile，就是「这个 Agent 长什么样」的四个开关）：")
    for axis in AXES:
        print("  %s = %s" % (axis, profile[axis]))
    print("加载范围：命中 %d ／ 现扫总数 %d（跳过 %d 条）" % (len(hit), len(entries), len(entries) - len(hit)))
    print("跳过判据：条目「适用条件」写了某轴的具体条件、而画像档位不满足。")
    if emit == "ids":
        print(" ".join(e["n"] for e in hit))
    else:
        by = {}
        for e in hit:
            by.setdefault(e["category"], []).append(e)
        for cat in sorted(by):
            print("\n## %s（%d 条）" % (cat, len(by[cat])))
            for e in by[cat]:
                print("- [ ] %s %s｜%s" % (e["n"], e["title"], e["fields"].get("检查问题", "（缺检查问题）")))
    return hit


# ---------------------------------------------------------------- 索引生成

def reverse_pattern_map(patterns):
    m = {}
    for p in patterns:
        for fm in p["guards"]:
            m.setdefault(fm, []).append(p["n"])
    return m


def case_map(root, entries):
    """案例 → 死法：从 01 各条的「案例」字段和 04 的标题反查，键用死法编号。"""
    out = {}
    for e in entries:
        c = e["fields"].get("案例", "")
        ids = CASE_ID_RE.findall(c)
        if ids:
            out[e["n"]] = ids
    return out


def render_index(root):
    entries = gate(root)
    c = counts_of(root)
    pats = parse_patterns(root)
    rev = reverse_pattern_map(pats)
    cases = case_map(root, entries)
    L = []
    ap = L.append
    ap("# 失败目录索引（自动生成件，勿手改）")
    ap("")
    ap("> 本件由 `scripts/fm_catalog.py render --apply` 从 `references/01-failure-modes/` 的条目**现扫**生成："
       "条数、号段、交叉引用、药柜对账全部来自条目本身，不在任何散文里另抄一份。")
    ap("> 手改无效（下次 render 会覆盖）；`fm_catalog.py check` 把本件登记为生成件，只在生成件里允许出现容量数字。")
    ap("> 生成命令：`py scripts/fm_catalog.py render --apply`　｜　体检命令：`py scripts/fm_catalog.py check`")
    ap("")
    ap("## 一、容量总账（现扫，全库唯一的数字落点之一）")
    ap("")
    ap("| 对象 | 条数 | 数法（可复算） |")
    ap("|---|---|---|")
    ap("| 死法（失败模式） | %d | 01 目录里行首 `### X-N ` 标题行之和 |" % c["fm"])
    ap("| 死法类别 | %d | 01 目录里非生成件的 `.md` 文件数（一文件一类） |" % c["classes"])
    ap("| 对策（药柜） | %d | 02 文件里行首 `### P-` 标题行数 |" % c["patterns"])
    ap("| 术语 | %d | 03 文件的表格数据行数（去表头与分隔行） |" % c["terms"])
    ap("| 事故案例 | %d | 04 文件里 `### R-`／`### F-` 标题行数 |" % c["cases"])
    ap("| 检查清单文件 | %d | 05 目录 `.md` 文件数（含 1 份留痕模板） |" % c["checklists"])
    ap("| 提问模板文件 | %d | 06 目录 `.md` 文件数 |" % c["prompts"])
    ap("")
    ap("## 二、分类与号段")
    ap("")
    ap("| 类别 | 前缀 | 文件 | 条数 | 号段 |")
    ap("|---|---|---|---|---|")
    for prefix in sorted(c["per_class"]):
        info = c["per_class"][prefix]
        nums = sorted(info["nums"])
        ap("| %s | `%s-` | `%s` | %d | %s |" % (
            info["category"], prefix, info["file"], info["n"],
            "%d…%d" % (nums[0], nums[-1]) if nums else "—"))
    ap("| **合计** | — | — | **%d** | — |" % c["fm"])
    ap("")
    ap("## 三、死法目录（ID → 适用条件 → 药柜 → 案例）")
    ap("")
    ap("「适用条件」四轴（轴名是筛选用的开关，值只允许受控词表）：副作用=任意／有写（这条死法要不要 Agent 真去写东西才会发生）、"
       "并发=任意／多流（要不要多个执行流或多 Agent 同时跑）、租户=任意／多租户（要不要多客户共享一套系统）、"
       "敏感度=任意／敏感（要不要涉及密钥或个人信息）。写「任意」＝任何画像都要查。")
    ap("")
    ap("| ID | 标题 | 副作用 | 并发 | 租户 | 敏感度 | 药柜 | 案例 |")
    ap("|---|---|---|---|---|---|---|---|")
    for e in entries:
        ax = e["axes"]
        ap("| %s | %s | %s | %s | %s | %s | %s | %s |" % (
            e["n"], e["title"], ax.get("副作用", "缺"), ax.get("并发", "缺"),
            ax.get("租户", "缺"), ax.get("敏感度", "缺"),
            "、".join(rev.get(e["n"], [])) or "—",
            "、".join(cases.get(e["n"], [])) or "—"))
    ap("")
    ap("## 四、药柜对账（02 的「防」字段 → 01 的编号，逐条现扫）")
    ap("")
    ap("| 对策 | 名称 | 防（现扫） |")
    ap("|---|---|---|")
    for p in pats:
        ap("| %s | %s | %s |" % (p["n"], p["name"], "、".join(p["guards"]) or "（防字段没写编号）"))
    ap("")
    uncovered = [e["n"] + " " + e["title"] for e in entries if e["n"] not in rev]
    ap("## 五、未成格清单（有方子、没成药）")
    ap("")
    ap("这些死法在 01 的「对策」字段里写了解法，但 02 药柜里没有一条对策的「防」字段点到它的编号——"
       "按五步工作流第 3 步「到 02 查最小实现」走的人会撞空。本清单由 render 现扫，不手填；"
       "补齐归属对策库维护轮次，不作为本目录的体检红项。")
    ap("")
    ap("| 现扫未成格条数 | %d |" % len(uncovered))
    ap("|---|---|")
    for line in uncovered:
        ap("| %s |" % line)
    ap("")
    ap("## 六、按画像选检查项（单故障不必全量穷举）")
    ap("")
    ap("```")
    ap("py scripts/fm_catalog.py select --side-effect 写 --concurrency 多 --tenant 多 --sensitivity 敏感")
    ap("py scripts/fm_catalog.py select --side-effect 写 --format ids")
    ap("py scripts/fm_catalog.py select --format checklist")
    ap("```")
    ap("")
    ap("四条命令的「加载范围」行各会打印命中数与总数——加载了什么、跳过了什么，是可观察的，不是号称读了全部。")
    ap("")
    ap("<!-- GENERATED-BY fm_catalog.py；本件之后的任何手改都会在下次 render 时被覆盖 -->")
    return "\n".join(L) + "\n"


def capacity_block(c):
    """SKILL.md 的 AUTO 容量块正文：数字一律来自现扫。"""
    cls = CN_INT.get(c["classes"], str(c["classes"]))
    return [
        AUTO_BEGIN,
        "> 一句话：Agent 不是功能跑通就完了，是「知道它会怎么死」之后才算完。本库**现扫**收录 "
        "**%d 条死法**（%s类）、**%d 条对策**、**%d 条术语**、**%d 例事故**、"
        "**%d 份检查清单文件**（含 1 份留痕模板）、**%d 份提问模板文件**——"
        "案例含企业级真实事故；设计时对着查、上线前对着勾、出事时对着排。"
        "以上数字由 `scripts/fm_catalog.py render --apply` 从条目现扫写入，全库只有本块与 "
        "`%s` 允许出现容量数字；设计新 Agent 先按画像跑 `select` 定范围（见「五步工作流」第一步），不必每次全量穷举。" % (
            c["fm"], cls, c["patterns"], c["terms"], c["cases"], c["checklists"], c["prompts"],
            FM_SUBDIR + "/" + INDEX_NAME),
        "> 术语收录对齐业界权威分类：UC Berkeley MAST（多 Agent 失败分类法，arXiv:2503.13657）、"
        "微软 Agentic AI 失败模式分类、MCP 安全通报（工具投毒／拉地毯／困惑代理人）、Simon Willison「致命三要素」。",
        AUTO_END,
    ]


def render_skill(root, block):
    path = Path(root) / "SKILL.md"
    if not path.is_file():
        die("找不到 SKILL.md：%s" % path)
    text = read_text(path)
    lines = text.splitlines()
    out, i, replaced = [], 0, False
    while i < len(lines):
        if lines[i].startswith("<!-- AUTO-BEGIN"):
            out.extend(block)
            while i < len(lines) and not lines[i].startswith("<!-- AUTO-END"):
                i += 1
            replaced = True
            i += 1
            continue
        out.append(lines[i])
        i += 1
    if not replaced:
        die("SKILL.md 里找不到 AUTO 容量块标记（%s）——先人工放置标记，再让脚本填内容；"
            "脚本拒绝自行猜测插入位置（防止把别人的段落圈进替换范围）" % AUTO_BEGIN[:24] + "…")
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def unified(text_a, text_b):
    return list(difflib.unified_diff(text_a.splitlines(), text_b.splitlines(),
                                     fromfile="现状", tofile="生成", lineterm="", n=1))


def atomic_write(path, text):
    """写后读回验收，不符即回滚原内容；不在技能目录里留 .bak 孤儿文件。"""
    original = path.read_bytes() if path.is_file() else None
    data = text.encode("utf-8")
    fd, tmp = tempfile.mkstemp(prefix="_fmtmp_", dir=str(path.parent))
    try:
        with io.open(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, str(path))
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    back = path.read_bytes()
    if back != data:
        if original is not None:
            path.write_bytes(original)
        raise SystemExit("红｜写后读回不一致，已从内存原件回滚：%s" % path)
    return len(data)


def do_render(root, apply_writes):
    c = counts_of(root)
    entries = gate(root)
    index_text = render_index(root)
    block = capacity_block(c)
    skill_new = render_skill(root, block)
    idx_path = Path(root) / FM_SUBDIR / INDEX_NAME
    idx_old = read_text(idx_path) if idx_path.is_file() else ""
    skill_old = read_text(Path(root) / "SKILL.md")
    print("现扫条目 %d 条 ／ %d 类，生成索引 %d 行" % (len(entries), c["classes"], len(index_text.splitlines())))
    targets = ((idx_path, idx_old, index_text, "索引生成件"), (Path(root) / "SKILL.md", skill_old, skill_new, "SKILL.md 容量块"))
    changed = 0
    for path, old, new, label in targets:
        if old == new:
            print("  %s：已一致，无需改动" % label)
            continue
        changed += 1
        d = unified(old, new)
        print("  %s：将变化 %d 行（+／- 摘要如下，最多列 12 行）" % (label, len(d)))
        for line in d[:12]:
            print("    " + line)
        if len(d) > 12:
            print("    …其余 %d 行见完整输出" % (len(d) - 12))
        if apply_writes:
            n = atomic_write(path, new)
            print("    已写入 %s（%d 字节，读回一致）" % (path.name, n))
    if not apply_writes:
        print(DRY)
    else:
        print("已落盘 %d 个目标；复跑 render 应显示两处「已一致」" % changed)
    return changed


# ---------------------------------------------------------------- 体检

def scan_prose(root):
    """参与复述计数检查的文件：SKILL.md + references/**（排除生成件）；CHANGELOG 属证词层，豁免并明写。"""
    files = [Path(root) / "SKILL.md"]
    files += sorted((Path(root) / "references").rglob("*.md"))
    out = []
    for p in files:
        if not p.is_file():
            continue
        if p.name == INDEX_NAME:
            continue
        rel = p.relative_to(Path(root)).as_posix()
        in_auto = False
        for ln, line in enumerate(read_text(p).splitlines(), 1):
            if line.startswith("<!-- AUTO-BEGIN"):
                in_auto = True
                continue
            if line.startswith("<!-- AUTO-END"):
                in_auto = False
                continue
            out.append((rel, ln, line, in_auto))
    return out


def template_one_dims(prompts_text):
    """只数「模板一」标题后第一个代码块里的编号行：维度清单在块内，块外的编号行是别的东西。"""
    m = re.search(r"^##\s*模板一[^\n]*$", prompts_text, re.M)
    if not m:
        return None
    fence = re.search(r"```\n(.*?)\n```", prompts_text[m.end():], re.S)
    if not fence:
        return None
    return len(re.findall(r"^ *\d+\.\s", fence.group(1), re.M))


def check(root, verbose=True):
    entries = gate(root)
    c = counts_of(root)
    red, warn = [], []
    ids = [e["n"] for e in entries]
    known_ids = set(ids)
    known_p = set(p["n"] for p in parse_patterns(root))
    known_case = set(x["n"] for x in parse_cases(root))

    # R1 编号唯一 + 连号 + 前缀合法
    seen = {}
    for e in entries:
        if e["n"] in seen:
            red.append("R1 编号重复：%s 同时出现在 %s:%d 与 %s:%d" % (e["n"], seen[e["n"]][1], seen[e["n"]][2], e["file"], e["lineno"]))
        seen[e["n"]] = (e["num"], e["file"], e["lineno"])
        if e["prefix"] not in ("LOOP", "ERR", "COST", "SEC", "CTX", "CONC", "GOV"):
            red.append("R1 前缀不在册：%s（%s:%d）——七类前缀见索引「分类与号段」" % (e["n"], e["file"], e["lineno"]))
    for prefix, info in sorted(c["per_class"].items()):
        nums = sorted(info["nums"])
        if nums and nums != list(range(1, len(nums) + 1)):
            red.append("R1 %s 类号段不连号：%s（现扫 %d 条）——编号规则要求接续该类最大号" % (prefix, nums, len(nums)))

    # R2 字段齐 + 适用条件受控词表
    for e in entries:
        missing = [f for f in REQUIRED_FIELDS if f not in e["fields"]]
        if missing:
            red.append("R2 %s 缺字段：%s（%s:%d）" % (e["n"], "、".join(missing), e["file"], e["lineno"]))
        for axis in AXES:
            v = e["axes"].get(axis)
            if v is None:
                red.append("R2 %s 的适用条件没有 %s 轴（%s:%d）——四轴齐了 select 才不会误筛" % (e["n"], axis, e["file"], e["lineno"]))
            elif v not in VOCAB[axis]:
                red.append("R2 %s 适用条件 %s=%s 不在受控词表 %s 内" % (e["n"], axis, v, "／".join(VOCAB[axis])))

    # R3 交叉引用：活文本里出现的编号必须指得到（CHANGELOG 属证词层，不追改）
    for rel, ln, line, in_auto in scan_prose(root):
        for ref in FM_ID_RE.findall(line):
            if ref not in known_ids:
                red.append("R3 死交叉引用：%s:%d 指向不存在的 %s" % (rel, ln, ref))
        for ref in P_ID_RE.findall(line):
            if ref not in known_p:
                red.append("R3 对策交叉引用：%s:%d 指向不存在的 %s" % (rel, ln, ref))
        for ref in CASE_ID_RE.findall(line):
            if ref not in known_case and not re.search(re.escape(ref) + r"\+", line):
                red.append("R3 案例交叉引用：%s:%d 指向不存在的 %s（追加说明里的「%s+」写法已豁免）" % (rel, ln, ref, ref))

    # R4 复述型容量数字：只允许出现在 AUTO 块与索引生成件里
    exempt = ("%s/%s" % (FM_SUBDIR, INDEX_NAME), "CHANGELOG.md")
    for rel, ln, line, in_auto in scan_prose(root):
        if rel in exempt or in_auto:
            continue
        for pat in CAPACITY_PATTERNS:
            for m in pat.finditer(line):
                if any(mk in clause_of(line, m.start()) for mk in EXTERNAL_CITE):
                    continue
                red.append("R4 复述型计数：%s:%d 在生成块之外写了「%s」——数字只该存在于生成块与索引，"
                           "散文里改指向（扩容防漂移的铁律管不住散文，只有取消第二处数字才管得住）" % (rel, ln, m.group(0).strip()))
                break
            else:
                continue
            break
    # R4b AUTO 块与索引里的数字必须等于现扫（这条能红：改一个数字就翻）
    skill_text = read_text(Path(root) / "SKILL.md")
    m = re.search(re.escape(AUTO_BEGIN) + r"(.*?)" + re.escape(AUTO_END), skill_text, re.S)
    if not m:
        red.append("R4b SKILL.md 缺 AUTO 容量块（跑 render --apply 生成）")
    else:
        body = m.group(1)
        pairs = re.findall(r"\*\*(\d+)\s*(条|份|例)\s*(检查清单文件|提问模板文件|死法|对策|术语|事故)\*\*", body)
        want = {"死法": c["fm"], "对策": c["patterns"], "术语": c["terms"], "事故": c["cases"],
                "检查清单文件": c["checklists"], "提问模板文件": c["prompts"]}
        if len(pairs) < len(want):
            red.append("R4b AUTO 块只数到 %d 组容量字段，应有 %d 组（缺项＝生成块被手改坏，跑 render --apply 重出）"
                       % (len(pairs), len(want)))
        for val, _unit, noun in pairs:
            if noun in want and int(val) != want[noun]:
                red.append("R4b 入口计数与条目数不一致：AUTO 块写 %s %s，现扫 %d → 跑 render --apply 重算"
                           % (noun, val, want[noun]))

    # R5 分类数与维度数
    for rel, ln, line, in_auto in scan_prose(root):
        if rel in exempt:
            continue
        for m2 in CLASS_RE.finditer(line):
            n = CN_NUM[m2.group(1)]
            if n != c["classes"]:
                red.append("R5 分类数过期：%s:%d 写「%s类死法」，现扫 %d 类" % (rel, ln, m2.group(1), c["classes"]))
    prompts_text = read_text(Path(root) / PROMPTS_MAIN_REL) if (Path(root) / PROMPTS_MAIN_REL).is_file() else ""
    dims = template_one_dims(prompts_text)
    if dims is None:
        warn.append("R5 模板一缺代码块围栏，维度数无法核验——不按整文件数编号行，那样会把别处的编号清单算进分母")
    else:
        for rel, ln, line, in_auto in scan_prose(root):
            if rel in exempt:
                continue
            for m2 in DIM_RE.finditer(line):
                if CN_NUM[m2.group(1)] != dims:
                    red.append("R5 维度数过期：%s:%d 写「%s维度」，模板一代码块实数 %d 个编号维度"
                               % (rel, ln, m2.group(1), dims))

    # R6 触发题接入目录（eval_queries.json 的 expected_ids 必须存在）
    eq = Path(root) / "eval_queries.json"
    if eq.is_file():
        try:
            data = json.loads(read_text(eq))
        except ValueError as exc:
            red.append("R6 eval_queries.json 不是合法 JSON：%s" % exc)
            data = []
        for item in data if isinstance(data, list) else []:
            for ref in item.get("expected_ids", []) or []:
                if ref not in known_ids:
                    red.append("R6 触发题 #%s 的 expected_ids 指向不存在的死法 %s" % (item.get("id"), ref))
        if isinstance(data, list) and data and all("expected_ids" in it for it in data):
            if verbose:
                print("ℹ️ R6 触发题 %d 条已全部接入目录（每条带 expected_ids，指不到条目即判红）" % len(data))
        else:
            warn.append("R6 eval_queries.json 有条目没写 expected_ids（触发题还没接入目录结构）")
    else:
        warn.append("R6 缺 eval_queries.json（触发判断题不在位）")

    # R7 可观察行为断言接入评估
    ob = Path(root) / "evals" / "observable_behaviors.json"
    ev = Path(root) / "evals" / "evals.json"
    if ob.is_file():
        try:
            behaviors = json.loads(read_text(ob))
        except ValueError as exc:
            behaviors = []
            red.append("R7 observable_behaviors.json 不是合法 JSON：%s" % exc)
        for b in behaviors if isinstance(behaviors, list) else []:
            for ref in b.get("failure_modes", []) or []:
                if ref not in known_ids:
                    red.append("R7 行为探针 %s 指向不存在的死法 %s" % (b.get("id"), ref))
            if not b.get("observable") or not b.get("pass_condition"):
                red.append("R7 行为探针 %s 缺 observable／pass_condition（没有可观察量＝不是行为评估）" % b.get("id"))
    else:
        red.append("R7 缺 evals/observable_behaviors.json——评估仍停在「回答里有没有某些词」，"
                   "没有可观察行为断言（审查 §二.14 点名的正是这一点）")
    if ev.is_file():
        try:
            scenes = json.loads(read_text(ev))
        except ValueError:
            scenes = []
        for s in scenes if isinstance(scenes, list) else []:
            if not s.get("observable_checks"):
                warn.append("R7 评估场景 #%s 还没有 observable_checks（只验表述、不验行为）" % s.get("id"))

    # R8 药柜覆盖（黄，不红）
    rev = reverse_pattern_map(parse_patterns(root))
    uncovered = [e["n"] for e in entries if e["n"] not in rev]
    if uncovered:
        warn.append("R8 %d 条死法在 02 药柜里没有独立对策格（有方子没成药，清单见索引第五节）：%s"
                    % (len(uncovered), "、".join(uncovered)))

    if verbose:
        print("现扫：死法 %d／%d 类｜对策 %d｜术语 %d｜案例 %d｜检查清单文件 %d｜提问模板 %d"
              % (c["fm"], c["classes"], c["patterns"], c["terms"], c["cases"], c["checklists"], c["prompts"]))
        print("豁免登记（不是盲区）：CHANGELOG.md＝证词层（历史数字不回改）；%s＝生成件本身；"
              "数字所在那个逗号级分句里带 %s 之一＝引文里的别人家数字，不是本库容量。"
              % (INDEX_NAME, "／".join(EXTERNAL_CITE)))
        print("红项 %d 条／警告 %d 条" % (len(red), len(warn)))
        for x in red:
            print(" 🔴 " + x)
        for x in warn:
            print(" 🟡 " + x)
    return red, warn


# ---------------------------------------------------------------- selftest（只用合成样例）

FIXTURE_FM = """# 失败模式：循环与终止（LOOP）

> 这一类死法的共同点：测试夹具。

### LOOP-1 活锁（无进展循环）
- 现象：反复调同一个工具，没有进展
- 术语：Livelock（活锁）
- 根因：模型陷入局部循环
- 对策：迭代上限 + 状态指纹
- 最坏后果：烧 token
- 检查问题：最近 N 轮状态有没有变化？
- 相关：Circuit breaker
- 适用条件：副作用=任意｜并发=任意｜租户=任意｜敏感度=任意

### LOOP-2 交替循环（A→B→A→B）
- 现象：两个状态来回切换，单独看每一步都在动
- 术语：Oscillation（交替振荡）
- 根因：只比对相邻一轮，周期为 2 的循环永远看不见
- 对策：滑动窗口周期检测
- 最坏后果：烧 token 且绕过单状态检测
- 检查问题：最近 K 轮里有没有长度为 2 的重复片段？
- 相关：State fingerprint
- 适用条件：副作用=任意｜并发=多流｜租户=任意｜敏感度=任意
"""

FIXTURE_PATTERNS = """# 对策模式库（02-patterns）

### P-01 迭代上限（Iteration limit）
- **防**：LOOP-1 活锁。

### P-02 状态指纹（State fingerprint）
- **防**：LOOP-1、LOOP-2。
"""

FIXTURE_SKILL = """---
name: fixture
---
# 夹具

%s
> 一句话：现扫 **2 条死法**、**2 条对策**、**1 条术语**、**1 例事故**、**1 份检查清单文件**、**1 份提问模板文件**。
%s

## 工作流
1. 打开「设计时检查」，按类逐项问（条数见索引，此处不复述）。

## 交付自检
- [ ] 全部死法问过（未命中注明理由）
""" % (AUTO_BEGIN, AUTO_END)

FIXTURE_EVAL_QUERIES = json.dumps([
    {"id": 1, "query": "Agent 反复调同一个工具停不下来", "should_trigger": True, "expected_ids": ["LOOP-1"]},
    {"id": 2, "query": "帮我打印 1 到 100 的偶数", "should_trigger": False, "expected_ids": []},
], ensure_ascii=False)

FIXTURE_BEHAVIORS = json.dumps([
    {"id": "B-01", "name": "循环真的停了", "observable": "第 K 轮后主循环不再发起新调用",
     "pass_condition": "K 不超过迭代上限，且退出原因是熔断触发而不是人工干预",
     "failure_modes": ["LOOP-1", "LOOP-2"]},
], ensure_ascii=False)

FIXTURE_SCENES = json.dumps(
    [{"id": 1, "prompt": "x", "observable_checks": ["B-01"]}], ensure_ascii=False)


def build_fixture(base):
    root = Path(base)
    (root / FM_SUBDIR).mkdir(parents=True, exist_ok=True)
    files = [
        (FM_SUBDIR + "/FM-循环与终止.md", FIXTURE_FM),
        (PATTERNS_REL, FIXTURE_PATTERNS),
        (TERMS_REL, "# 术语\n\n| 术语 | 中文 | 一句话解释 | 详见 |\n|---|---|---|---|\n"
                    "| Livelock | 活锁 | 忙但没进展 | LOOP-1 |\n"),
        (INCIDENTS_REL, "# 案例\n\n### R-01 夹具案例\n"),
        (CHECKLISTS_SUBDIR + "/设计时检查.md", "# 设计时\n"),
        (PROMPTS_MAIN_REL, "## 模板一：二维度问题清单\n\n```\n1. 循环与终止\n2. 错误与恢复\n```\n"),
        ("eval_queries.json", FIXTURE_EVAL_QUERIES),
        ("evals/observable_behaviors.json", FIXTURE_BEHAVIORS),
        ("evals/evals.json", FIXTURE_SCENES),
        ("SKILL.md", FIXTURE_SKILL),
    ]
    for rel, content in files:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        write_lf(p, content)
    return root


def fresh_fixture(tag):
    return build_fixture(tempfile.mkdtemp(prefix="fm_%s_" % tag))


def reds_of(root):
    return check(root, verbose=False)[0]


def run_selftest():
    """结构＝一把绿基线 + 每条判据各有一个「只破坏它」的红样本。
    只证绿不证红的尺子等于没尺子（恒真判据），所以 A0 先要求基线 0 红。"""
    results = []

    def say(name, ok, detail=""):
        results.append((name, ok))
        print(("[PASS] " if ok else "[FAIL] ") + name + ("｜" + detail if detail else ""))

    base = fresh_fixture("base")
    base_red = reds_of(base)
    say("A0 夹具基线 0 红（否则后面每条红断言都可能被基线冤枉）", not base_red,
        "基线红 %d 条：%s" % (len(base_red), " / ".join(x[:30] for x in base_red)))

    es = parse_entries(base)
    say("A1 夹具解析出 2 条死法", len(es) == 2, "现扫 %d" % len(es))
    say("A2 适用条件解析成四轴", bool(es) and es[0]["axes"].get("副作用") == "任意"
        and es[1]["axes"].get("并发") == "多流")

    hit_single = do_select_quiet(base, dict(PROFILE_DEFAULT))
    hit_multi = do_select_quiet(base, dict(PROFILE_DEFAULT, **{"并发": "多"}))
    say("A3 单流画像只加载 1 条（跳过要求并发的 LOOP-2）", len(hit_single) == 1, "命中 %d" % len(hit_single))
    say("A3b 并发画像加载 2 条", len(hit_multi) == 2, "命中 %d" % len(hit_multi))
    say("A3c 命中＋跳过＝现扫总数（选择器不静默丢条目）",
        len(hit_single) <= len(es) and len(hit_multi) == len(es))

    c = counts_of(base)
    got = (c["fm"], c["classes"], c["patterns"], c["terms"], c["cases"])
    say("A4 容量总账＝死法2／类1／对策2／术语1／案例1", got == (2, 1, 2, 1, 1), "实得 %s" % str(got))
    say("A5 索引生成件含 LOOP-2 目录行", "| LOOP-2 |" in render_index(base))

    grown = fresh_fixture("grow")
    fm1 = grown / FM_SUBDIR / "FM-循环与终止.md"
    old = read_text(fm1)
    extra = ("\n### LOOP-3 新加的一条\n- 现象：x\n- 术语：y\n- 根因：z\n- 对策：迭代上限\n"
             "- 最坏后果：w\n- 检查问题：q？\n- 相关：r\n"
             "- 适用条件：副作用=任意｜并发=任意｜租户=任意｜敏感度=任意\n")
    write_lf(fm1, old + extra)
    c3 = counts_of(grown)
    say("A6 新增一条后现扫变 3 条", c3["fm"] == 3, "现扫 %d" % c3["fm"])
    rendered = render_skill(grown, capacity_block(c3))
    say("A7 入口容量块随条目重算成 3 条（旧值 2 无残留）",
        "**3 条死法**" in rendered and "**2 条死法**" not in rendered)
    say("A7b render 幂等（同输入再算一次内容逐字相同）",
        rendered == render_skill(grown, capacity_block(counts_of(grown))))
    say("A7c 索引里的合计跟着变成 3", "| **合计** | — | — | **3** | — |" in render_index(grown))

    r4 = fresh_fixture("r4")
    write_lf(r4 / CHECKLISTS_SUBDIR / "设计时检查.md",
             "# 设计时\n\n> 用法：把死法（2 条）逐项问。\n")
    say("A8 生成块之外复述条数 ⇒ 判红（数字对也红：第二处数字本身就是漂移点）",
        any("R4 复述型计数" in x for x in reds_of(r4)))
    r4b = fresh_fixture("r4b")
    write_lf(r4b / "SKILL.md", read_text(r4b / "SKILL.md").replace("**2 条死法**", "**5 条死法**"))
    say("A9 入口写 5 而条目实有 2 ⇒ 判红「入口计数与条目数不一致」",
        any("入口计数与条目数不一致" in x for x in reds_of(r4b)))
    r4c = fresh_fixture("r4c")
    write_lf(r4c / "CHANGELOG.md", "# CHANGELOG\n\n## 扩容 2→3\n- 曾有 2 条死法（历史数字）\n")
    say("A10 CHANGELOG 里的旧数字不判红（证词层豁免，豁免面在输出里明写）",
        not any("R4 复述型计数" in x for x in reds_of(r4c)))
    say("A10b 生成块内的正确数字不判红（同尺子的不冤枉面）",
        not any("R4 复述型计数" in x for x in reds_of(base)))
    r4d = fresh_fixture("r4d")
    write_lf(r4d / TERMS_REL, "# 术语\n\n| 术语 | 中文 | 一句话解释 | 详见 |\n|---|---|---|---|\n"
             "| MAST | 多 Agent 失败分类法 | UC Berkeley 提出，16 种失败模式三大类（arXiv:2503.13657） | LOOP-1 |\n")
    say("A8b 外部权威自带的数字不判红（别人家的分类数＝引文，不是本库容量）",
        not any("R4 复述型计数" in x for x in reds_of(r4d)))
    r4e = fresh_fixture("r4e")
    write_lf(r4e / TERMS_REL, "# 术语\n\n| 术语 | 中文 | 一句话解释 | 详见 |\n|---|---|---|---|\n"
             "| Self | 本库条数 | 本库收录 3 种死法 | LOOP-1 |\n")
    say("A8c 同一形状但写的是本库自己的条数 ⇒ 仍判红（豁免只给引文，不给自述）",
        any("R4 复述型计数" in x for x in reds_of(r4e)))
    r4f = fresh_fixture("r4f")
    write_lf(r4f / TERMS_REL, "# 术语\n\n| 术语 | 中文 | 一句话解释 | 详见 |\n|---|---|---|---|\n"
             "| MAST | 多 Agent 失败分类法 | 本库对齐 MAST，收录 3 种死法 | LOOP-1 |\n")
    say("A8d 同句引文、后半句自述：豁免不外溢到别的分句（本库条数照样判红）",
        any("R4 复述型计数" in x for x in reds_of(r4f)))

    r3 = fresh_fixture("r3")
    write_lf(r3 / CHECKLISTS_SUBDIR / "设计时检查.md", "# 设计时\n\n- 见 LOOP-77。\n")
    say("A11 指向不存在的死法编号 ⇒ 判红", any("R3 死交叉引用" in x for x in reds_of(r3)))
    r3b = fresh_fixture("r3b")
    write_lf(r3b / INCIDENTS_REL, "# 案例\n\n### R-01 夹具案例\n\n> 追加编号 R-02+；构造案例 F-05+。\n")
    say("A12 「F-05+」这种接续写法不判红（豁免只给带加号的）",
        not any("R3 案例交叉引用" in x for x in reds_of(r3b)))
    r3c = fresh_fixture("r3c")
    write_lf(r3c / INCIDENTS_REL, "# 案例\n\n### R-01 夹具案例\n\n> 见 F-09。\n")
    say("A12b 裸写不存在的案例编号 ⇒ 判红", any("R3 案例交叉引用" in x for x in reds_of(r3c)))
    r3d = fresh_fixture("r3d")
    write_lf(r3d / CHECKLISTS_SUBDIR / "设计时检查.md", "# 设计时\n\n- 见 P-77。\n")
    say("A12c 指向不存在的对策编号 ⇒ 判红", any("R3 对策交叉引用" in x for x in reds_of(r3d)))
    r3e = fresh_fixture("r3e")
    write_lf(r3e / PATTERNS_REL, read_text(r3e / PATTERNS_REL).replace("P-01", "P-77"))
    say("A12d 改的是定义（P-01 改名 P-77）不报「悬空指针」——引用没动，尺子不把改名说成断链",
        not any("R3 对策交叉引用" in x for x in reds_of(r3e)))

    empty = tempfile.mkdtemp(prefix="fm_empty_")
    (Path(empty) / FM_SUBDIR).mkdir(parents=True, exist_ok=True)
    try:
        gate(empty)
        say("A13 空库不判通过（分母闸，退 2）", False, "gate 居然返回了")
    except SystemExit:
        say("A13 空库不判通过（分母闸，退 2）", True)

    r2 = fresh_fixture("r2")
    t = read_text(r2 / FM_SUBDIR / "FM-循环与终止.md")
    say("A13b 夹具替换串在磁盘上真的存在（先验尺子的输入面，防止替换静默不命中＝假红/假绿）",
        "- 适用条件：副作用=任意｜并发=多流｜租户=任意｜敏感度=任意\n" in t)
    write_lf(r2 / FM_SUBDIR / "FM-循环与终止.md",
             t.replace("- 适用条件：副作用=任意｜并发=多流｜租户=任意｜敏感度=任意\n", ""))
    say("A14 条目缺适用条件整行 ⇒ 判红", any("缺字段" in x for x in reds_of(r2)))
    r2b = fresh_fixture("r2b")
    write_lf(r2b / FM_SUBDIR / "FM-循环与终止.md",
             t.replace("副作用=任意｜并发=多流", "副作用=随便｜并发=多流"))
    say("A15 适用条件写了词表外的值 ⇒ 判红", any("不在受控词表" in x for x in reds_of(r2b)))

    dup = fresh_fixture("dup")
    write_lf(dup / FM_SUBDIR / "FM-循环与终止.md",
             t + "\n### LOOP-1 重号的一条\n- 现象：x\n- 术语：y\n- 根因：z\n- 对策：w\n- 最坏后果：v\n"
                 "- 检查问题：q？\n- 相关：r\n- 适用条件：副作用=任意｜并发=任意｜租户=任意｜敏感度=任意\n")
    say("A16 编号重复 ⇒ 判红", any("编号重复" in x for x in reds_of(dup)))
    gap = fresh_fixture("gap")
    write_lf(gap / FM_SUBDIR / "FM-循环与终止.md", t.replace("### LOOP-2 ", "### LOOP-5 "))
    say("A17 号段不连号 ⇒ 判红", any("号段不连号" in x for x in reds_of(gap)))

    dim = fresh_fixture("dim")
    write_lf(dim / PROMPTS_MAIN_REL, "## 模板一：三维度问题清单\n\n```\n1. 循环与终止\n2. 错误与恢复\n```\n")
    say("A18 标题写「三维度」而模板一实有 2 个编号维度 ⇒ 判红",
        any("R5 维度数过期" in x for x in reds_of(dim)))
    dim2 = fresh_fixture("dim2")
    write_lf(dim2 / PROMPTS_MAIN_REL,
             "## 模板一：二维度问题清单\n\n```\n1. 循环与终止\n2. 错误与恢复\n```\n\n"
             "## 别的编号清单\n1. 甲\n2. 乙\n3. 丙\n")
    say("A18d 代码块外另有编号行不虚增分母（维度只数模板一那个块）",
        not any("R5 维度数过期" in x for x in reds_of(dim2)))
    dim3 = fresh_fixture("dim3")
    write_lf(dim3 / PROMPTS_MAIN_REL, "## 模板一：二维度问题清单\n\n没有围栏的正文。\n")
    say("A18e 模板一缺围栏 ⇒ 记警告，不硬猜一个维度数（读不到就不判）",
        any("无法核验" in x for x in check(dim3, verbose=False)[1]))
    cls = fresh_fixture("cls")
    write_lf(cls / CHECKLISTS_SUBDIR / "设计时检查.md", "# 设计时\n\n> 用法：把六类死法逐条问一遍。\n")
    say("A18b 分类数过期（写六类、实有一类）⇒ 判红", any("R5 分类数过期" in x for x in reds_of(cls)))
    say("A18c 「这一类死法」这种指示用法不判红（尺子不冤枉散文）",
        not any("R5 分类数过期" in x for x in reds_of(base)))

    ob = fresh_fixture("ob")
    (ob / "evals" / "observable_behaviors.json").unlink()
    say("A19 缺可观察行为断言文件 ⇒ 判红（评估退回「只验回答里有没有某些词」就报警）",
        any("observable_behaviors.json" in x for x in reds_of(ob)))
    ob2 = fresh_fixture("ob2")
    write_lf(ob2 / "evals" / "observable_behaviors.json", json.dumps(
        [{"id": "B-02", "name": "缺可观察量的探针", "failure_modes": ["LOOP-1"]}], ensure_ascii=False))
    say("A20 探针没有 observable／pass_condition ⇒ 判红", any("缺 observable" in x for x in reds_of(ob2)))
    ob3 = fresh_fixture("ob3")
    write_lf(ob3 / "evals" / "observable_behaviors.json", json.dumps(
        [{"id": "B-03", "name": "指向不存在的死法", "observable": "x", "pass_condition": "y",
          "failure_modes": ["LOOP-88"]}], ensure_ascii=False))
    say("A20b 行为探针指向不存在的死法 ⇒ 判红", any("R7 行为探针" in x for x in reds_of(ob3)))
    eq = fresh_fixture("eq")
    write_lf(eq / "eval_queries.json", json.dumps(
        [{"id": 1, "query": "x", "should_trigger": True, "expected_ids": ["LOOP-99"]}], ensure_ascii=False))
    say("A21 触发题指向不存在的死法 ⇒ 判红", any("R6 触发题" in x for x in reds_of(eq)))
    eq2 = fresh_fixture("eq2")
    write_lf(eq2 / "eval_queries.json", json.dumps(
        [{"id": 1, "query": "x", "should_trigger": True}], ensure_ascii=False))
    say("A22 触发题还没接入目录 ⇒ 至少记警告（不静默放过）",
        any("expected_ids" in x for x in check(eq2, verbose=False)[1]))

    noblk = fresh_fixture("noblk")
    write_lf(noblk / "SKILL.md", "# 夹具\n\n一句话：不复述数字。\n")
    say("A23 SKILL.md 没有 AUTO 标记 ⇒ check 判红（脚本不许自己猜插入位置）",
        any("缺 AUTO 容量块" in x for x in reds_of(noblk)))
    try:
        render_skill(noblk, capacity_block(counts_of(noblk)))
        say("A23b render 遇到无标记的 SKILL.md 直接报错退出", False, "它居然返回了文本")
    except SystemExit:
        say("A23b render 遇到无标记的 SKILL.md 直接报错退出", True)

    n_pass = sum(1 for _n, ok in results if ok)
    print("断言 %d 条：PASS=%d FAIL=%d" % (len(results), n_pass, len(results) - n_pass))
    return 0 if n_pass == len(results) else 1


def do_select_quiet(root, profile):
    entries = gate(root)
    return [e for e in entries if applicable(e, profile)]


# ---------------------------------------------------------------- CLI

def main(argv=None):
    ap = argparse.ArgumentParser(description="失败模式目录的现扫生成器与体检器（默认只读）",
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("--root", default=None, help="技能根目录（默认取本脚本所在目录的上一层；不给就去默认值，不静默用当前目录）")
    sub = ap.add_subparsers(dest="cmd")
    p_scan = sub.add_parser("scan", help="解析条目")
    p_scan.add_argument("--json", action="store_true")
    sub.add_parser("counts", help="打印现扫容量总账")
    p_sel = sub.add_parser("select", help="按画像只挑适用条目")
    p_sel.add_argument("--side-effect", dest="副作用", choices=PROFILE_CHOICES["副作用"], default=PROFILE_DEFAULT["副作用"],
                       help="工具副作用：这个 Agent 会不会真去写东西（无＝只读）")
    p_sel.add_argument("--concurrency", dest="并发", choices=PROFILE_CHOICES["并发"], default=PROFILE_DEFAULT["并发"],
                       help="并发：单流还是多流／多 Agent")
    p_sel.add_argument("--tenant", dest="租户", choices=PROFILE_CHOICES["租户"], default=PROFILE_DEFAULT["租户"],
                       help="租户：单租户还是多租户共享")
    p_sel.add_argument("--sensitivity", dest="敏感度", choices=PROFILE_CHOICES["敏感度"], default=PROFILE_DEFAULT["敏感度"],
                       help="数据敏感度：会不会碰到密钥或个人信息")
    p_sel.add_argument("--format", dest="fmt", choices=("table", "ids", "checklist"), default="checklist")
    p_ren = sub.add_parser("render", help="生成索引与 SKILL 容量块（默认干跑）")
    p_ren.add_argument("--apply", action="store_true", help="真写盘")
    sub.add_parser("check", help="结构体检")
    sub.add_parser("selftest", help="合成夹具自测（不碰真实库）")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 2
    if a.cmd == "selftest":
        return run_selftest()
    root = Path(a.root) if a.root else SKILL_ROOT
    if not root.is_dir():
        die("技能根不存在：%s" % root)
    if a.cmd == "scan":
        entries = gate(root)
        if a.json:
            print(json.dumps(entries, ensure_ascii=False, indent=1, default=str))
        else:
            for e in entries:
                print("%s｜%s｜%s" % (e["n"], e["title"], e["fields"].get("适用条件", "（缺适用条件）")))
            print("共 %d 条" % len(entries))
        return 0
    if a.cmd == "counts":
        c = counts_of(root)
        gate(root)
        for k in ("fm", "classes", "patterns", "terms", "cases", "checklists", "prompts"):
            print("%s = %d" % (k, c[k]))
        for prefix in sorted(c["per_class"]):
            print("  %s（%s）= %d 条" % (prefix, c["per_class"][prefix]["category"], c["per_class"][prefix]["n"]))
        return 0
    if a.cmd == "select":
        profile = {"副作用": a.副作用, "并发": a.并发, "租户": a.租户, "敏感度": a.敏感度}
        do_select(root, profile, a.fmt)
        return 0
    if a.cmd == "render":
        do_render(root, a.apply)
        return 0
    if a.cmd == "check":
        red, warn = check(root)
        return 1 if red else 0
    die("未知子命令：%s" % a.cmd)


if __name__ == "__main__":
    sys.exit(main())
